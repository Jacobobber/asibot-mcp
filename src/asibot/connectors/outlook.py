"""Outlook connector: email and calendar via Microsoft Graph API."""

import logging
import re
from datetime import datetime, timedelta, timezone

from mcp.server.fastmcp import Context, FastMCP

from asibot.config import settings
from asibot.connectors import microsoft
from asibot.connectors.base import Connector

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
        if not all([settings.sharepoint_tenant_id, settings.sharepoint_client_id]):
            return

        @mcp.tool()
        async def outlook_search_email(query: str, ctx: Context, limit: int = 10) -> str:
            """Search your Outlook email.

            Args:
                query: Search query (subject, body, sender, etc.)
                limit: Max results (default: 10)
            """
            client, uid, err = await microsoft.require_graph_client(ctx, "outlook", "read")
            if err:
                return err
            r = await client.get(f"{GRAPH}/me/messages", params={"$search": f'"{query}"', "$top": limit, "$select": "subject,from,receivedDateTime,bodyPreview"})
            r.raise_for_status()
            msgs = r.json().get("value", [])
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
            client, uid, err = await microsoft.require_graph_client(ctx, "outlook", "read")
            if err:
                return err
            r = await client.get(f"{GRAPH}/me/messages/{message_id}", params={"$select": "subject,from,toRecipients,receivedDateTime,body"})
            r.raise_for_status()
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
            client, uid, err = await microsoft.require_graph_client(ctx, "outlook", "write")
            if err:
                return err
            r = await client.post(f"{GRAPH}/me/sendMail", json={
                "message": {"subject": subject, "body": {"contentType": "Text", "content": body}, "toRecipients": [{"emailAddress": {"address": to}}]},
                "saveToSentItems": True,
            })
            r.raise_for_status()
            return f'Email sent to {to}: "{subject}"'

        @mcp.tool()
        async def outlook_recent_emails(ctx: Context, limit: int = 10, folder: str = "inbox") -> str:
            """Get your most recent emails.

            Args:
                limit: Number of emails (default: 10)
                folder: Folder — inbox, sentitems, drafts (default: inbox)
            """
            client, uid, err = await microsoft.require_graph_client(ctx, "outlook", "read")
            if err:
                return err
            r = await client.get(f"{GRAPH}/me/mailFolders/{folder}/messages", params={"$top": limit, "$orderby": "receivedDateTime desc", "$select": "subject,from,receivedDateTime,bodyPreview,isRead"})
            r.raise_for_status()
            msgs = r.json().get("value", [])
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
            r = await client.get(f"{GRAPH}/me/calendarView", params={"startDateTime": now.isoformat(), "endDateTime": end.isoformat(), "$top": 50, "$orderby": "start/dateTime", "$select": "subject,start,end,location,organizer,isAllDay"})
            r.raise_for_status()
            events = r.json().get("value", [])
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
