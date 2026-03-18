"""Google Workspace connector: Drive and Calendar via Google REST APIs."""

import logging

import httpx
from mcp.server.fastmcp import Context, FastMCP
# Note: httpx kept for gdrive_read_file which does conditional branching

from asibot import token_store, validation
from asibot.connectors.base import Connector

logger = logging.getLogger(__name__)
DRIVE_API = "https://www.googleapis.com/drive/v3"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"


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
            client, uid, err = token_store.require_service(ctx, "google", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "GET", f"{DRIVE_API}/files",
                service="Google Drive", action="search",
                params={"q": f"fullText contains '{query}'", "pageSize": limit, "fields": "files(id,name,mimeType,modifiedTime,webViewLink)"},
            )
            if err:
                return err
            files = r.json().get("files", [])
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
            client, uid, err = token_store.require_service(ctx, "google", level="read")
            if err:
                return err
            r, err = await token_store.safe_request(
                client, "GET", f"{DRIVE_API}/files",
                service="Google Drive", action="list files",
                params={"q": f"'{folder_id}' in parents and trashed = false", "pageSize": limit, "fields": "files(id,name,mimeType,modifiedTime,size)", "orderBy": "modifiedTime desc"},
            )
            if err:
                return err
            files = r.json().get("files", [])
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
            client, uid, err = token_store.require_service(ctx, "google", level="read")
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
            client, uid, err = token_store.require_service(ctx, "google", level="read")
            if err:
                return err
            from datetime import datetime, timedelta, timezone

            now = datetime.now(timezone.utc)
            time_min = now.isoformat()
            time_max = (now + timedelta(days=days)).isoformat()
            r, err = await token_store.safe_request(
                client, "GET", f"{CALENDAR_API}/calendars/primary/events",
                service="Google Calendar", action="events",
                params={"timeMin": time_min, "timeMax": time_max, "maxResults": limit, "singleEvents": True, "orderBy": "startTime"},
            )
            if err:
                return err
            events = r.json().get("items", [])
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
