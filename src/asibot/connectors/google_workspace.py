"""Google Workspace connector: Drive and Calendar via Google REST APIs."""

import logging

import httpx
from mcp.server.fastmcp import Context, FastMCP
# Note: httpx kept for gdrive_read_file which does conditional branching

from asibot import token_store, validation
from asibot.connectors.base import Connector
from asibot.connectors.pagination import collect, paginate_cursor

logger = logging.getLogger(__name__)
DRIVE_API = "https://www.googleapis.com/drive/v3"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"
GMAIL_API = "https://www.googleapis.com/gmail/v1"
SHEETS_API = "https://sheets.googleapis.com/v4"


class GoogleWorkspaceConnector(Connector):
    def __init__(self, config=None):
        super().__init__(name="google", config=config)

    async def connect(self):
        logger.info("Google Workspace: ready (per-user OAuth token)")

    async def disconnect(self):
        pass

    async def fetch_documents(self):
        return []

    def register_tools(self, mcp: FastMCP):

        @mcp.tool()
        async def gdrive_search(query: str, ctx: Context, limit: int = 10) -> str:
            """Search files in Google Drive.

            Args:
                query: Search query (supports Drive search syntax)
                limit: Max results (default: 10)
            """
            err = validation.validate_query(query, "query")
            if err:
                return err
            limit = validation.validate_limit(limit)
            client, uid, err = await token_store.require_service(ctx, "google", level="read")
            if err:
                return err
            pages = paginate_cursor(
                client, f"{DRIVE_API}/files",
                method="GET",
                service="Google Drive", action="search",
                params={"q": f"fullText contains '{query}'", "fields": "files(id,name,mimeType,modifiedTime,webViewLink),nextPageToken"},
                results_key="files",
                cursor_response_key="nextPageToken",
                cursor_request_key="pageToken",
                cursor_in="params",
                page_size_param="pageSize",
                page_size=min(limit, 100),
            )
            files = await collect(pages, limit)
            if not files:
                return "No files found."
            return "\n\n".join(
                f"{f.get('name', 'Untitled')}\n  ID: {f['id']} | Type: {f.get('mimeType', '?')} | Modified: {f.get('modifiedTime', '?')[:10]}\n  Link: {f.get('webViewLink', '?')}"
                for f in files
            )

        @mcp.tool()
        async def gdrive_list_files(ctx: Context, folder_id: str = "root", limit: int = 20) -> str:
            """List files in a Google Drive folder.

            Args:
                folder_id: Folder ID (default: root)
                limit: Max results (default: 20)
            """
            if folder_id and folder_id != "root":
                err = validation.validate_id(folder_id, "folder_id")
                if err:
                    return err
            limit = validation.validate_limit(limit)
            client, uid, err = await token_store.require_service(ctx, "google", level="read")
            if err:
                return err
            pages = paginate_cursor(
                client, f"{DRIVE_API}/files",
                method="GET",
                service="Google Drive", action="list files",
                params={"q": f"'{folder_id}' in parents and trashed = false", "fields": "files(id,name,mimeType,modifiedTime,size),nextPageToken", "orderBy": "modifiedTime desc"},
                results_key="files",
                cursor_response_key="nextPageToken",
                cursor_request_key="pageToken",
                cursor_in="params",
                page_size_param="pageSize",
                page_size=min(limit, 100),
            )
            files = await collect(pages, limit)
            if not files:
                return "No files found in this folder."
            return "\n".join(
                f"{f.get('name', 'Untitled')}  ({f.get('mimeType', '?')}, modified {f.get('modifiedTime', '?')[:10]})\n  ID: {f['id']}"
                for f in files
            )

        @mcp.tool()
        async def gdrive_read_file(file_id: str, ctx: Context) -> str:
            """Read the text content of a Google Drive file (Docs, Sheets, or plain text).

            Args:
                file_id: The file ID
            """
            err = validation.validate_id(file_id, "file_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "google", level="read")
            if err:
                return err
            # First get file metadata to determine type
            try:
                meta_r = await client.get(
                    f"{DRIVE_API}/files/{file_id}",
                    params={"fields": "id,name,mimeType"},
                )
                meta_r.raise_for_status()
            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                return token_store.format_api_error("Google Drive", "read file", e)
            meta = meta_r.json()
            mime = meta.get("mimeType", "")
            name = meta.get("name", "Untitled")

            # Google Docs/Sheets/Slides: export as plain text
            try:
                if mime.startswith("application/vnd.google-apps."):
                    export_mime = "text/plain"
                    if "spreadsheet" in mime:
                        export_mime = "text/csv"
                    r = await client.get(
                        f"{DRIVE_API}/files/{file_id}/export",
                        params={"mimeType": export_mime},
                    )
                else:
                    # Regular file: download content
                    r = await client.get(f"{DRIVE_API}/files/{file_id}", params={"alt": "media"})
                r.raise_for_status()
            except (httpx.HTTPStatusError, httpx.RequestError) as e:
                return token_store.format_api_error("Google Drive", "read file", e)
            content = r.text
            if len(content) > 15000:
                content = content[:15000] + "\n\n... (truncated)"
            return f"--- {name} ---\n\n{content}"

        @mcp.tool()
        async def gcalendar_events(ctx: Context, days: int = 7, limit: int = 20) -> str:
            """List upcoming Google Calendar events.

            Args:
                days: Number of days to look ahead (default: 7)
                limit: Max results (default: 20)
            """
            client, uid, err = await token_store.require_service(ctx, "google", level="read")
            if err:
                return err
            from datetime import datetime, timedelta, timezone

            now = datetime.now(timezone.utc)
            time_min = now.isoformat()
            time_max = (now + timedelta(days=days)).isoformat()
            pages = paginate_cursor(
                client, f"{CALENDAR_API}/calendars/primary/events",
                method="GET",
                service="Google Calendar", action="events",
                params={"timeMin": time_min, "timeMax": time_max, "singleEvents": True, "orderBy": "startTime"},
                results_key="items",
                cursor_response_key="nextPageToken",
                cursor_request_key="pageToken",
                cursor_in="params",
                page_size_param="maxResults",
                page_size=min(limit, 250),
            )
            events = await collect(pages, limit)
            if not events:
                return f"No events in the next {days} days."
            lines = []
            for e in events:
                start_obj = e.get("start", {})
                start = start_obj.get("dateTime", start_obj.get("date", "?"))
                end_obj = e.get("end", {})
                end = end_obj.get("dateTime", end_obj.get("date", "?"))
                attendees = e.get("attendees", [])
                att_str = f" | {len(attendees)} attendees" if attendees else ""
                lines.append(f"{e.get('summary', 'No title')}\n  {start} -> {end}{att_str}")
            return "\n\n".join(lines)

        @mcp.tool()
        async def gdrive_get_file_info(file_id: str, ctx: Context) -> str:
            """Get detailed metadata for a Google Drive file.

            Args:
                file_id: The file ID
            """
            err = validation.validate_id(file_id, "file_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "google", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "GET", f"{DRIVE_API}/files/{file_id}",
                service="Google Drive", action="get file info",
                params={"fields": "id,name,mimeType,modifiedTime,size,owners,shared,webViewLink,createdTime"},
            )
            if err:
                return err
            f = r.json()
            owners = ", ".join(o.get("displayName", "?") for o in f.get("owners", []))
            return (
                f"{f.get('name', 'Untitled')}\n"
                f"  ID: {f.get('id', '?')}\n"
                f"  Type: {f.get('mimeType', '?')}\n"
                f"  Created: {f.get('createdTime', '?')[:10] if f.get('createdTime') else '?'}\n"
                f"  Modified: {f.get('modifiedTime', '?')[:10] if f.get('modifiedTime') else '?'}\n"
                f"  Size: {f.get('size', '?')}\n"
                f"  Shared: {f.get('shared', '?')}\n"
                f"  Owners: {owners or '?'}\n"
                f"  Link: {f.get('webViewLink', '?')}"
            )

        @mcp.tool()
        async def gcalendar_get_event(event_id: str, ctx: Context) -> str:
            """Get details of a Google Calendar event.

            Args:
                event_id: The event ID
            """
            err = validation.validate_id(event_id, "event_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "google", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "GET", f"{CALENDAR_API}/calendars/primary/events/{event_id}",
                service="Google Calendar", action="get event",
            )
            if err:
                return err
            e = r.json()
            start_obj = e.get("start", {})
            start = start_obj.get("dateTime", start_obj.get("date", "?"))
            end_obj = e.get("end", {})
            end = end_obj.get("dateTime", end_obj.get("date", "?"))
            attendees = e.get("attendees", [])
            att_list = ", ".join(a.get("email", "?") for a in attendees[:10])
            organizer = e.get("organizer", {}).get("email", "?")
            return (
                f"{e.get('summary', 'No title')}\n"
                f"  Start: {start}\n"
                f"  End: {end}\n"
                f"  Location: {e.get('location', 'None')}\n"
                f"  Organizer: {organizer}\n"
                f"  Description: {e.get('description', 'None')}\n"
                f"  Attendees ({len(attendees)}): {att_list or 'None'}"
            )

        @mcp.tool()
        async def gcalendar_create_event(summary: str, start: str, end: str, ctx: Context, description: str = "", attendees: str = "") -> str:
            """Create a Google Calendar event.

            Args:
                summary: Event title
                start: Start time (ISO 8601 dateTime)
                end: End time (ISO 8601 dateTime)
                description: Event description (optional)
                attendees: Comma-separated email addresses (optional)
            """
            err = validation.validate_content(summary, "summary")
            if err:
                return err
            err = validation.validate_content(start, "start")
            if err:
                return err
            err = validation.validate_content(end, "end")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "google", level="write")
            if err:
                return err
            event_body = {
                "summary": summary,
                "start": {"dateTime": start},
                "end": {"dateTime": end},
            }
            if description:
                event_body["description"] = description
            if attendees:
                emails = [e.strip() for e in attendees.split(",") if e.strip()]
                for email in emails:
                    email_err = validation.validate_email_address(email)
                    if email_err:
                        return email_err
                event_body["attendees"] = [{"email": e} for e in emails]
            r, err = await token_store.safe_request(
                client, "POST", f"{CALENDAR_API}/calendars/primary/events",
                service="Google Calendar", action="create event",
                json=event_body,
            )
            if err:
                return err
            data = r.json()
            return f"Event created. ID: {data.get('id', '?')} | Link: {data.get('htmlLink', '?')}"

        # --- Gmail Tools ---

        @mcp.tool()
        async def gmail_search(query: str, ctx: Context, limit: int = 10) -> str:
            """Search Gmail messages.

            Args:
                query: Gmail search query (supports Gmail search syntax)
                limit: Max results (default: 10)
            """
            err = validation.validate_query(query, "query")
            if err:
                return err
            limit = validation.validate_limit(limit)
            client, uid, err = await token_store.require_service(ctx, "google", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "GET", f"{GMAIL_API}/users/me/messages",
                service="Gmail", action="search",
                params={"q": query, "maxResults": limit},
            )
            if err:
                return err
            messages = r.json().get("messages", [])
            if not messages:
                return "No messages found."
            # Fetch metadata for each message
            lines = []
            for msg in messages[:limit]:
                m_r, m_err = await token_store.safe_request(
                    client, "GET", f"{GMAIL_API}/users/me/messages/{msg['id']}",
                    service="Gmail", action="get message",
                    params={"format": "metadata", "metadataHeaders": "Subject,From,Date"},
                )
                if m_err:
                    lines.append(f"Message {msg['id']} (could not fetch metadata)")
                    continue
                m_data = m_r.json()
                headers = {h["name"]: h["value"] for h in m_data.get("payload", {}).get("headers", [])}
                lines.append(
                    f"ID: {msg['id']}\n  From: {headers.get('From', '?')}\n  Subject: {headers.get('Subject', '?')}\n  Date: {headers.get('Date', '?')}"
                )
            return "\n\n".join(lines)

        @mcp.tool()
        async def gmail_read_email(message_id: str, ctx: Context) -> str:
            """Read the content of a Gmail message.

            Args:
                message_id: The message ID
            """
            err = validation.validate_id(message_id, "message_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "google", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "GET", f"{GMAIL_API}/users/me/messages/{message_id}",
                service="Gmail", action="read email",
                params={"format": "full"},
            )
            if err:
                return err
            data = r.json()
            headers = {h["name"]: h["value"] for h in data.get("payload", {}).get("headers", [])}
            # Extract body text
            import base64
            body_text = ""
            payload = data.get("payload", {})
            if payload.get("body", {}).get("data"):
                body_text = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
            elif payload.get("parts"):
                for part in payload["parts"]:
                    if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                        body_text = base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
                        break
            if len(body_text) > 15000:
                body_text = body_text[:15000] + "\n\n... (truncated)"
            return (
                f"From: {headers.get('From', '?')}\n"
                f"To: {headers.get('To', '?')}\n"
                f"Subject: {headers.get('Subject', '?')}\n"
                f"Date: {headers.get('Date', '?')}\n\n"
                f"{body_text}"
            )

        @mcp.tool()
        async def gmail_send_email(to: str, subject: str, body: str, ctx: Context, cc: str = "", bcc: str = "") -> str:
            """Send an email via Gmail.

            Args:
                to: Recipient email address
                subject: Email subject
                body: Email body (plain text)
                cc: CC recipients (comma-separated, optional)
                bcc: BCC recipients (comma-separated, optional)
            """
            email_err = validation.validate_email_address(to)
            if email_err:
                return email_err
            err = validation.validate_content(subject, "subject")
            if err:
                return err
            err = validation.validate_content(body, "body")
            if err:
                return err
            if cc:
                for addr in cc.split(","):
                    email_err = validation.validate_email_address(addr.strip())
                    if email_err:
                        return email_err
            if bcc:
                for addr in bcc.split(","):
                    email_err = validation.validate_email_address(addr.strip())
                    if email_err:
                        return email_err
            client, uid, err = await token_store.require_service(ctx, "google", level="write")
            if err:
                return err
            import base64
            # Build RFC 2822 message
            lines = [f"To: {to}", f"Subject: {subject}"]
            if cc:
                lines.append(f"Cc: {cc}")
            if bcc:
                lines.append(f"Bcc: {bcc}")
            lines.append("Content-Type: text/plain; charset=utf-8")
            lines.append("")
            lines.append(body)
            raw_message = "\r\n".join(lines)
            encoded = base64.urlsafe_b64encode(raw_message.encode("utf-8")).decode("ascii")
            r, err = await token_store.safe_request(
                client, "POST", f"{GMAIL_API}/users/me/messages/send",
                service="Gmail", action="send email",
                json={"raw": encoded},
            )
            if err:
                return err
            data = r.json()
            return f"Email sent. Message ID: {data.get('id', '?')}"

        @mcp.tool()
        async def gmail_reply(message_id: str, body: str, ctx: Context) -> str:
            """Reply to a Gmail message.

            Args:
                message_id: ID of the message to reply to
                body: Reply body (plain text)
            """
            err = validation.validate_id(message_id, "message_id")
            if err:
                return err
            err = validation.validate_content(body, "body")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "google", level="write")
            if err:
                return err
            # Fetch original message to get headers
            orig_r, orig_err = await token_store.safe_request(
                client, "GET", f"{GMAIL_API}/users/me/messages/{message_id}",
                service="Gmail", action="get original message",
                params={"format": "metadata", "metadataHeaders": "Subject,From,To,Message-ID"},
            )
            if orig_err:
                return orig_err
            orig = orig_r.json()
            headers = {h["name"]: h["value"] for h in orig.get("payload", {}).get("headers", [])}
            thread_id = orig.get("threadId", "")
            reply_to = headers.get("From", "")
            subject = headers.get("Subject", "")
            if not subject.lower().startswith("re:"):
                subject = f"Re: {subject}"
            import base64
            lines = [
                f"To: {reply_to}",
                f"Subject: {subject}",
                f"In-Reply-To: {headers.get('Message-ID', '')}",
                f"References: {headers.get('Message-ID', '')}",
                "Content-Type: text/plain; charset=utf-8",
                "",
                body,
            ]
            raw_message = "\r\n".join(lines)
            encoded = base64.urlsafe_b64encode(raw_message.encode("utf-8")).decode("ascii")
            r, err = await token_store.safe_request(
                client, "POST", f"{GMAIL_API}/users/me/messages/send",
                service="Gmail", action="reply",
                json={"raw": encoded, "threadId": thread_id},
            )
            if err:
                return err
            data = r.json()
            return f"Reply sent. Message ID: {data.get('id', '?')}"

        # --- Google Drive Write Tools ---

        @mcp.tool()
        async def gdrive_create_folder(name: str, ctx: Context, parent_id: str = "root") -> str:
            """Create a folder in Google Drive.

            Args:
                name: Folder name
                parent_id: Parent folder ID (default: root)
            """
            err = validation.validate_content(name, "name")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "google", level="write")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "POST", f"{DRIVE_API}/files",
                service="Google Drive", action="create folder",
                json={
                    "name": name,
                    "mimeType": "application/vnd.google-apps.folder",
                    "parents": [parent_id],
                },
            )
            if err:
                return err
            data = r.json()
            return f"Folder created. ID: {data.get('id', '?')} | Name: {data.get('name', '?')}"

        @mcp.tool()
        async def gdrive_upload_file(name: str, content: str, ctx: Context, mime_type: str = "text/plain", parent_id: str = "root") -> str:
            """Upload a file to Google Drive.

            Args:
                name: File name
                content: Base64-encoded file content (or plain text for text files)
                mime_type: MIME type of the file (default: text/plain)
                parent_id: Parent folder ID (default: root)
            """
            err = validation.validate_content(name, "name")
            if err:
                return err
            err = validation.validate_content(content, "content")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "google", level="write")
            if err:
                return err
            import base64
            import json
            try:
                file_bytes = base64.b64decode(content)
            except Exception:
                file_bytes = content.encode("utf-8")
            metadata = json.dumps({"name": name, "parents": [parent_id]})
            # Use multipart upload
            boundary = "asibot_upload_boundary"
            body = (
                f"--{boundary}\r\n"
                f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
                f"{metadata}\r\n"
                f"--{boundary}\r\n"
                f"Content-Type: {mime_type}\r\n\r\n"
            ).encode("utf-8") + file_bytes + f"\r\n--{boundary}--".encode("utf-8")
            r, err = await token_store.safe_request(
                client, "POST",
                "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
                service="Google Drive", action="upload file",
                content=body,
                headers={"Content-Type": f"multipart/related; boundary={boundary}"},
            )
            if err:
                return err
            data = r.json()
            return f"File uploaded. ID: {data.get('id', '?')} | Name: {data.get('name', '?')}"

        @mcp.tool()
        async def gdrive_delete_file(file_id: str, ctx: Context) -> str:
            """Delete a file from Google Drive.

            Args:
                file_id: The file ID to delete
            """
            err = validation.validate_id(file_id, "file_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "google", level="write")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "DELETE", f"{DRIVE_API}/files/{file_id}",
                service="Google Drive", action="delete file",
            )
            if err:
                return err
            return f"File {file_id} deleted."

        @mcp.tool()
        async def gdrive_share_file(file_id: str, email: str, ctx: Context, role: str = "reader") -> str:
            """Share a Google Drive file with a user.

            Args:
                file_id: The file ID to share
                email: Email address of the user to share with
                role: Permission role: 'reader', 'writer', or 'commenter' (default: reader)
            """
            err = validation.validate_id(file_id, "file_id")
            if err:
                return err
            email_err = validation.validate_email_address(email)
            if email_err:
                return email_err
            valid_roles = ("reader", "writer", "commenter")
            if role not in valid_roles:
                return f"Invalid role. Must be one of: {', '.join(valid_roles)}"
            client, uid, err = await token_store.require_service(ctx, "google", level="write")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "POST", f"{DRIVE_API}/files/{file_id}/permissions",
                service="Google Drive", action="share file",
                json={"type": "user", "role": role, "emailAddress": email},
            )
            if err:
                return err
            return f"File {file_id} shared with {email} as {role}."

        # --- Google Sheets Tools ---

        @mcp.tool()
        async def gsheets_read(spreadsheet_id: str, range: str, ctx: Context) -> str:
            """Read data from a Google Sheets spreadsheet.

            Args:
                spreadsheet_id: The spreadsheet ID
                range: Cell range in A1 notation (e.g. 'Sheet1!A1:D10')
            """
            err = validation.validate_id(spreadsheet_id, "spreadsheet_id")
            if err:
                return err
            err = validation.validate_content(range, "range")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "google", level="read")
            if err:
                return err
            from urllib.parse import quote as url_quote
            encoded_range = url_quote(range, safe="")
            r, err = await token_store.safe_request(
                client, "GET",
                f"{SHEETS_API}/spreadsheets/{spreadsheet_id}/values/{encoded_range}",
                service="Google Sheets", action="read",
            )
            if err:
                return err
            data = r.json()
            values = data.get("values", [])
            if not values:
                return "No data found in the specified range."
            lines = []
            for row in values:
                lines.append("\t".join(str(cell) for cell in row))
            return f"Range: {data.get('range', range)}\n\n" + "\n".join(lines)

        @mcp.tool()
        async def gsheets_update(spreadsheet_id: str, range: str, values: str, ctx: Context) -> str:
            """Update cells in a Google Sheets spreadsheet.

            Args:
                spreadsheet_id: The spreadsheet ID
                range: Cell range in A1 notation (e.g. 'Sheet1!A1:D10')
                values: JSON string of 2D array (e.g. '[["A1","B1"],["A2","B2"]]')
            """
            err = validation.validate_id(spreadsheet_id, "spreadsheet_id")
            if err:
                return err
            err = validation.validate_content(range, "range")
            if err:
                return err
            err = validation.validate_content(values, "values")
            if err:
                return err
            import json
            try:
                values_list = json.loads(values)
            except (json.JSONDecodeError, ValueError):
                return "Invalid values: must be a valid JSON 2D array."
            if not isinstance(values_list, list):
                return "Invalid values: must be a JSON array of arrays."
            client, uid, err = await token_store.require_service(ctx, "google", level="write")
            if err:
                return err
            from urllib.parse import quote as url_quote
            encoded_range = url_quote(range, safe="")
            r, err = await token_store.safe_request(
                client, "PUT",
                f"{SHEETS_API}/spreadsheets/{spreadsheet_id}/values/{encoded_range}",
                service="Google Sheets", action="update",
                params={"valueInputOption": "USER_ENTERED"},
                json={"range": range, "values": values_list},
            )
            if err:
                return err
            data = r.json()
            return f"Updated {data.get('updatedCells', '?')} cells in {data.get('updatedRange', range)}."
