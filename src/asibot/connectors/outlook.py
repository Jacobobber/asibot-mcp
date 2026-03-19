"""Outlook connector: email and calendar via Microsoft Graph API."""

import logging
import re
from datetime import datetime, timedelta, timezone

from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store, validation
from asibot.config import settings
from asibot.connectors import microsoft
from asibot.connectors.base import Connector
from asibot.connectors.pagination import collect, paginate_odata

logger = logging.getLogger(__name__)
GRAPH = microsoft.GRAPH_BASE


class OutlookConnector(Connector):
    def __init__(self, config=None):
        super().__init__(name="outlook", config=config)

    async def connect(self):
        logger.info("Outlook: ready (Microsoft SSO)")

    async def disconnect(self):
        pass

    async def fetch_documents(self):
        return []

    def register_tools(self, mcp: FastMCP):
        if not all([settings.ms365_tenant_id, settings.ms365_client_id]):
            return

        @mcp.tool()
        async def outlook_search_email(query: str, ctx: Context, limit: int = 10) -> str:
            """Search your Outlook email.

            Args:
                query: Search query (subject, body, sender, etc.)
                limit: Max results (default: 10)
            """
            err = validation.validate_query(query, "query")
            if err:
                return err
            limit = validation.validate_limit(limit)
            client, uid, err = await microsoft.require_graph_client(ctx, "outlook", "read")
            if err:
                return err
            pages = paginate_odata(
                client, f"{GRAPH}/me/messages",
                service="Outlook", action="search email",
                params={"$search": f'"{query}"', "$top": min(limit, 50), "$select": "subject,from,receivedDateTime,bodyPreview"},
            )
            msgs = await collect(pages, limit)
            if not msgs:
                return "No emails found."
            lines = []
            for m in msgs:
                sender = m.get("from", {}).get("emailAddress", {})
                lines.append(f"Subject: {m.get('subject', 'No subject')}\n  From: {sender.get('name', '?')} <{sender.get('address', '?')}>\n  Date: {m.get('receivedDateTime', '?')}\n  Preview: {m.get('bodyPreview', '')[:200]}\n  ID: {m.get('id', '')}")
            return "\n\n".join(lines)

        @mcp.tool()
        async def outlook_read_email(message_id: str, ctx: Context) -> str:
            """Read the full content of an email.

            Args:
                message_id: The email message ID (from search results)
            """
            err = validation.validate_id(message_id, "message_id")
            if err:
                return err
            client, uid, err = await microsoft.require_graph_client(ctx, "outlook", "read")
            if err:
                return err
            r, err = await token_store.safe_request(client, "GET", f"{GRAPH}/me/messages/{message_id}", service="Outlook", action="read email", params={"$select": "subject,from,toRecipients,receivedDateTime,body"})
            if err:
                return err
            m = r.json()
            sender = m.get("from", {}).get("emailAddress", {})
            to = ", ".join(r.get("emailAddress", {}).get("address", "?") for r in m.get("toRecipients", []))
            body = m.get("body", {}).get("content", "")
            if m.get("body", {}).get("contentType") == "html":
                body = re.sub(r"<[^>]+>", " ", body)
                body = re.sub(r"\s+", " ", body).strip()
            return f"Subject: {m.get('subject', 'No subject')}\nFrom: {sender.get('name', '?')} <{sender.get('address', '?')}>\nTo: {to}\nDate: {m.get('receivedDateTime', '?')}\n\n{body}"

        @mcp.tool()
        async def outlook_send_email(to: str, subject: str, body: str, ctx: Context) -> str:
            """Send an email from your Outlook account.

            Args:
                to: Recipient email address
                subject: Email subject
                body: Email body (plain text)
            """
            err = validation.validate_email_address(to)
            if err:
                return err
            err = validation.validate_content(subject, "subject")
            if err:
                return err
            err = validation.validate_content(body, "body")
            if err:
                return err
            client, uid, err = await microsoft.require_graph_client(ctx, "outlook", "write")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "POST", f"{GRAPH}/me/sendMail",
                service="Outlook", action="send email",
                json={"message": {"subject": subject, "body": {"contentType": "Text", "content": body}, "toRecipients": [{"emailAddress": {"address": to}}]}, "saveToSentItems": True},
            )
            if err:
                return err
            return f'Email sent to {to}: "{subject}"'

        @mcp.tool()
        async def outlook_list_folders(ctx: Context) -> str:
            """List your Outlook mail folders."""
            client, uid, err = await microsoft.require_graph_client(ctx, "outlook", "read")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "GET", f"{GRAPH}/me/mailFolders",
                service="Outlook", action="list folders",
                params={"$top": 50},
            )
            if err:
                return err
            folders = r.json().get("value", [])
            if not folders:
                return "No folders found."
            lines = []
            for f in folders:
                lines.append(
                    f"{f.get('displayName', '?')}\n"
                    f"  ID: {f.get('id', '')}\n"
                    f"  Total: {f.get('totalItemCount', 0)}  Unread: {f.get('unreadItemCount', 0)}"
                )
            return "\n\n".join(lines)

        @mcp.tool()
        async def outlook_get_attachments(message_id: str, ctx: Context) -> str:
            """List attachments on an email (metadata only, no binary content).

            Args:
                message_id: The email message ID
            """
            err = validation.validate_id(message_id, "message_id")
            if err:
                return err
            client, uid, err = await microsoft.require_graph_client(ctx, "outlook", "read")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "GET", f"{GRAPH}/me/messages/{message_id}/attachments",
                service="Outlook", action="get attachments",
            )
            if err:
                return err
            attachments = r.json().get("value", [])
            if not attachments:
                return "No attachments."
            lines = []
            for a in attachments:
                lines.append(
                    f"{a.get('name', '?')}\n"
                    f"  Type: {a.get('contentType', '?')}\n"
                    f"  Size: {a.get('size', 0):,} bytes"
                )
            return "\n\n".join(lines)

        @mcp.tool()
        async def calendar_create_event(subject: str, start: str, end: str, ctx: Context, body: str = "", attendees: str = "") -> str:
            """Create a calendar event.

            Args:
                subject: Event subject/title
                start: Start datetime (ISO format, e.g. 2024-03-15T10:00:00)
                end: End datetime (ISO format, e.g. 2024-03-15T11:00:00)
                body: Optional event body/description
                attendees: Optional comma-separated email addresses
            """
            err = validation.validate_content(subject, "subject")
            if err:
                return err
            client, uid, err = await microsoft.require_graph_client(ctx, "calendar", "write")
            if err:
                return err
            event = {
                "subject": subject,
                "start": {"dateTime": start, "timeZone": "UTC"},
                "end": {"dateTime": end, "timeZone": "UTC"},
            }
            if body:
                event["body"] = {"contentType": "text", "content": body}
            if attendees:
                emails = [e.strip() for e in attendees.split(",") if e.strip()]
                for email in emails:
                    err = validation.validate_email_address(email)
                    if err:
                        return err
                event["attendees"] = [{"emailAddress": {"address": e}, "type": "required"} for e in emails]
            r, err = await token_store.safe_request(
                client, "POST", f"{GRAPH}/me/events",
                service="Calendar", action="create event",
                json=event,
            )
            if err:
                return err
            created = r.json()
            return f"Event created: {created.get('subject', subject)}\n  ID: {created.get('id', '?')}\n  When: {start} — {end}"

        _ALLOWED_FOLDERS = frozenset({"inbox", "sentitems", "drafts", "deleteditems", "junkemail", "archive"})

        @mcp.tool()
        async def outlook_recent_emails(ctx: Context, limit: int = 10, folder: str = "inbox") -> str:
            """Get your most recent emails.

            Args:
                limit: Number of emails (default: 10)
                folder: Folder — inbox, sentitems, drafts, deleteditems, junkemail, archive (default: inbox)
            """
            err = validation.validate_folder_name(folder, _ALLOWED_FOLDERS)
            if err:
                return err
            limit = validation.validate_limit(limit)
            client, uid, err = await microsoft.require_graph_client(ctx, "outlook", "read")
            if err:
                return err
            pages = paginate_odata(
                client, f"{GRAPH}/me/mailFolders/{folder}/messages",
                service="Outlook", action="recent emails",
                params={"$top": min(limit, 50), "$orderby": "receivedDateTime desc", "$select": "subject,from,receivedDateTime,bodyPreview,isRead"},
            )
            msgs = await collect(pages, limit)
            if not msgs:
                return f"No emails in {folder}."
            lines = []
            for m in msgs:
                sender = m.get("from", {}).get("emailAddress", {})
                read = "" if m.get("isRead") else " [UNREAD]"
                lines.append(f"{m.get('subject', 'No subject')}{read}\n  From: {sender.get('name', '?')}\n  Date: {m.get('receivedDateTime', '?')[:16]}\n  ID: {m.get('id', '')}")
            return "\n\n".join(lines)

        @mcp.tool()
        async def calendar_events(ctx: Context, days: int = 7) -> str:
            """Get your upcoming calendar events.

            Args:
                days: Days to look ahead (default: 7)
            """
            client, uid, err = await microsoft.require_graph_client(ctx, "calendar", "read")
            if err:
                return err
            now = datetime.now(timezone.utc)
            end = now + timedelta(days=days)
            pages = paginate_odata(
                client, f"{GRAPH}/me/calendarView",
                service="Calendar", action="events",
                params={"startDateTime": now.isoformat(), "endDateTime": end.isoformat(), "$top": 50, "$orderby": "start/dateTime", "$select": "subject,start,end,location,organizer,isAllDay"},
            )
            events = await collect(pages, 200)
            if not events:
                return f"No events in the next {days} days."
            lines = []
            for e in events:
                start = e.get("start", {}).get("dateTime", "?")[:16]
                end_t = e.get("end", {}).get("dateTime", "?")[:16]
                loc = e.get("location", {}).get("displayName", "")
                org = e.get("organizer", {}).get("emailAddress", {}).get("name", "")
                loc_str = f"\n  Location: {loc}" if loc else ""
                lines.append(f"{e.get('subject', 'No title')}\n  When: {start} — {end_t}{loc_str}\n  Organizer: {org}")
            return "\n\n".join(lines)
