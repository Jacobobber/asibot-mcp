"""Zoom connector: meetings and recordings via Zoom REST API."""

import asyncio
import logging
import time

import httpx
from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store, validation
from asibot.connectors.base import Connector
from asibot.connectors.pagination import collect, paginate_cursor

logger = logging.getLogger(__name__)
API = "https://api.zoom.us/v2"
TOKEN_URL = "https://zoom.us/oauth/token"

# Token cache: account_id -> (token, expires_at)
_token_cache: dict[str, tuple[str, float]] = {}
_token_locks: dict[str, asyncio.Lock] = {}
_TOKEN_MARGIN = 300  # refresh 5 min before expiry


async def _get_access_token(creds) -> str:
    """Exchange Server-to-Server OAuth credentials for an access token (cached)."""
    account_id = creds["account_id"]

    # Per-account lock prevents duplicate token fetches under concurrent load
    lock = _token_locks.setdefault(account_id, asyncio.Lock())
    async with lock:
        cached = _token_cache.get(account_id)
        if cached:
            token, expires_at = cached
            if time.time() < expires_at - _TOKEN_MARGIN:
                return token

        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.post(
                TOKEN_URL,
                params={"grant_type": "account_credentials", "account_id": account_id},
                auth=(creds["client_id"], creds["client_secret"]),
            )
            r.raise_for_status()
            data = r.json()
            token = data.get("access_token")
            if not token:
                raise ValueError("Zoom OAuth response missing access_token")
            expires_in = data.get("expires_in", 3600)
            _token_cache[account_id] = (token, time.time() + expires_in)
            return token


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
            client, uid, err = token_store.require_service(ctx, "zoom", level="read")
            if err:
                return err
            creds = token_store.get_credentials(uid, "zoom")
            try:
                token = await _get_access_token(creds)
            except (httpx.HTTPStatusError, httpx.RequestError, ValueError) as e:
                return token_store.format_api_error("Zoom", "authenticate", e)
            pages = paginate_cursor(
                client, f"{API}/users/me/meetings",
                method="GET",
                service="Zoom", action="list meetings",
                params={"type": "upcoming"},
                results_key="meetings",
                cursor_response_key="next_page_token",
                cursor_request_key="next_page_token",
                cursor_in="params",
                page_size_param="page_size",
                page_size=min(limit, 100),
                headers={"Authorization": f"Bearer {token}"},
            )
            meetings = await collect(pages, limit)
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
            client, uid, err = token_store.require_service(ctx, "zoom", level="read")
            if err:
                return err
            creds = token_store.get_credentials(uid, "zoom")
            try:
                token = await _get_access_token(creds)
            except (httpx.HTTPStatusError, httpx.RequestError, ValueError) as e:
                return token_store.format_api_error("Zoom", "authenticate", e)
            r, err = await token_store.safe_request(
                client, "GET", f"{API}/meetings/{meeting_id}",
                service="Zoom", action="get meeting",
                headers={"Authorization": f"Bearer {token}"},
            )
            if err:
                return err
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
            if from_date:
                err = validation.validate_date(from_date, "from_date")
                if err:
                    return err
            if to_date:
                err = validation.validate_date(to_date, "to_date")
                if err:
                    return err
            limit = validation.validate_limit(limit)
            client, uid, err = token_store.require_service(ctx, "zoom", level="read")
            if err:
                return err
            creds = token_store.get_credentials(uid, "zoom")
            try:
                token = await _get_access_token(creds)
            except (httpx.HTTPStatusError, httpx.RequestError, ValueError) as e:
                return token_store.format_api_error("Zoom", "authenticate", e)
            rec_params: dict = {}
            if from_date:
                rec_params["from"] = from_date
            if to_date:
                rec_params["to"] = to_date
            pages = paginate_cursor(
                client, f"{API}/users/me/recordings",
                method="GET",
                service="Zoom", action="list recordings",
                params=rec_params,
                results_key="meetings",
                cursor_response_key="next_page_token",
                cursor_request_key="next_page_token",
                cursor_in="params",
                page_size_param="page_size",
                page_size=min(limit, 100),
                headers={"Authorization": f"Bearer {token}"},
            )
            meetings = await collect(pages, limit)
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
