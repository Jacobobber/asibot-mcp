"""Zoom connector: meetings and recordings via Zoom REST API."""

import logging

import httpx
from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store
from asibot.connectors.base import Connector

logger = logging.getLogger(__name__)
API = "https://api.zoom.us/v2"
TOKEN_URL = "https://zoom.us/oauth/token"


def _make_client(creds):
    if not creds.get("account_id") or not creds.get("client_id") or not creds.get("client_secret"):
        return None
    return httpx.AsyncClient(timeout=30.0)


async def _get_access_token(creds) -> str:
    """Exchange Server-to-Server OAuth credentials for an access token."""
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.post(
            TOKEN_URL,
            params={"grant_type": "account_credentials", "account_id": creds["account_id"]},
            auth=(creds["client_id"], creds["client_secret"]),
        )
        r.raise_for_status()
        return r.json()["access_token"]


class ZoomConnector(Connector):
    def __init__(self, config=None):
        super().__init__(name="zoom", config=config)

    async def connect(self):
        logger.info("Zoom: ready (per-user Server-to-Server OAuth)")

    async def disconnect(self):
        pass

    async def fetch_documents(self):
        return []

    def register_tools(self, mcp: FastMCP):

        @mcp.tool()
        async def zoom_list_meetings(ctx: Context, limit: int = 10) -> str:
            """List upcoming Zoom meetings.

            Args:
                limit: Max results (default: 10)
            """
            client, uid, err = token_store.require_service(ctx, "zoom", _make_client, "read")
            if err:
                return err
            creds = token_store.get_credentials(uid, "zoom")
            token = await _get_access_token(creds)
            r = await client.get(
                f"{API}/users/me/meetings",
                headers={"Authorization": f"Bearer {token}"},
                params={"page_size": limit, "type": "upcoming"},
            )
            r.raise_for_status()
            meetings = r.json().get("meetings", [])
            if not meetings:
                return "No upcoming meetings found."
            return "\n\n".join(
                f"{m.get('topic', 'Untitled')}\n  ID: {m['id']} | Start: {m.get('start_time', '?')} | Duration: {m.get('duration', '?')} min"
                for m in meetings
            )

        @mcp.tool()
        async def zoom_get_meeting(meeting_id: int, ctx: Context) -> str:
            """Get full details of a Zoom meeting.

            Args:
                meeting_id: The Zoom meeting ID
            """
            client, uid, err = token_store.require_service(ctx, "zoom", _make_client, "read")
            if err:
                return err
            creds = token_store.get_credentials(uid, "zoom")
            token = await _get_access_token(creds)
            r = await client.get(
                f"{API}/meetings/{meeting_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            r.raise_for_status()
            m = r.json()
            return (
                f"{m.get('topic', 'Untitled')}\n"
                f"ID: {m['id']} | Status: {m.get('status', '?')}\n"
                f"Start: {m.get('start_time', '?')} | Duration: {m.get('duration', '?')} min\n"
                f"Timezone: {m.get('timezone', '?')}\n"
                f"Join URL: {m.get('join_url', '?')}\n"
                f"Agenda: {m.get('agenda', 'None')}"
            )

        @mcp.tool()
        async def zoom_list_recordings(ctx: Context, from_date: str = "", to_date: str = "", limit: int = 10) -> str:
            """List Zoom cloud recordings.

            Args:
                from_date: Start date (YYYY-MM-DD). Defaults to 30 days ago.
                to_date: End date (YYYY-MM-DD). Defaults to today.
                limit: Max results (default: 10)
            """
            client, uid, err = token_store.require_service(ctx, "zoom", _make_client, "read")
            if err:
                return err
            creds = token_store.get_credentials(uid, "zoom")
            token = await _get_access_token(creds)
            params: dict = {"page_size": limit}
            if from_date:
                params["from"] = from_date
            if to_date:
                params["to"] = to_date
            r = await client.get(
                f"{API}/users/me/recordings",
                headers={"Authorization": f"Bearer {token}"},
                params=params,
            )
            r.raise_for_status()
            meetings = r.json().get("meetings", [])
            if not meetings:
                return "No recordings found."
            lines = []
            for m in meetings:
                files = m.get("recording_files", [])
                file_info = ", ".join(f.get("file_type", "?") for f in files) if files else "no files"
                lines.append(
                    f"{m.get('topic', 'Untitled')}\n  ID: {m.get('id', '?')} | Start: {m.get('start_time', '?')[:16]} | Files: {file_info}"
                )
            return "\n\n".join(lines)
