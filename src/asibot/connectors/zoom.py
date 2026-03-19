"""Zoom connector: meetings and recordings via Zoom REST API."""

import logging
import time
import urllib.parse

import httpx
from mcp.server.fastmcp import Context, FastMCP

from asibot import token_store, validation
from asibot.connectors.base import Connector
from asibot.connectors.pagination import collect, paginate_cursor

logger = logging.getLogger(__name__)
API = "https://api.zoom.us/v2"
TOKEN_URL = "https://zoom.us/oauth/token"


async def _get_access_token(creds: dict) -> str:
    """Exchange S2S OAuth credentials for an access token (cached, locked)."""
    return await token_store.get_s2s_token(
        cache_key=f"zoom:{creds['account_id']}",
        token_url=TOKEN_URL,
        grant_data={"grant_type": "account_credentials", "account_id": creds["account_id"]},
        auth=(creds["client_id"], creds["client_secret"]),
        service_name="Zoom",
        send_as_params=True,
    )


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
            client, uid, err = await token_store.require_service(ctx, "zoom", level="read")
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
            client, uid, err = await token_store.require_service(ctx, "zoom", level="read")
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
            client, uid, err = await token_store.require_service(ctx, "zoom", level="read")
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

        @mcp.tool()
        async def zoom_list_participants(meeting_id: str, ctx: Context) -> str:
            """List participants of a past Zoom meeting.

            Args:
                meeting_id: The Zoom meeting ID
            """
            err = validation.validate_id(meeting_id, "meeting_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "zoom", level="read")
            if err:
                return err
            creds = token_store.get_credentials(uid, "zoom")
            try:
                token = await _get_access_token(creds)
            except (httpx.HTTPStatusError, httpx.RequestError, ValueError) as e:
                return token_store.format_api_error("Zoom", "authenticate", e)
            encoded_id = urllib.parse.quote(meeting_id, safe="")
            r, err = await token_store.safe_request(
                client, "GET", f"{API}/past_meetings/{encoded_id}/participants",
                service="Zoom", action="list participants",
                headers={"Authorization": f"Bearer {token}"},
            )
            if err:
                return err
            participants = r.json().get("participants", [])
            if not participants:
                return "No participants found."
            return "\n".join(
                f"{p.get('name', 'Unknown')} | Email: {p.get('user_email', '?')} | Joined: {p.get('join_time', '?')} | Duration: {p.get('duration', '?')} min"
                for p in participants
            )

        @mcp.tool()
        async def zoom_list_past_meetings(ctx: Context, from_date: str = "", to_date: str = "", limit: int = 10) -> str:
            """List past Zoom meetings.

            Args:
                from_date: Start date (YYYY-MM-DD, optional)
                to_date: End date (YYYY-MM-DD, optional)
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
            client, uid, err = await token_store.require_service(ctx, "zoom", level="read")
            if err:
                return err
            creds = token_store.get_credentials(uid, "zoom")
            try:
                token = await _get_access_token(creds)
            except (httpx.HTTPStatusError, httpx.RequestError, ValueError) as e:
                return token_store.format_api_error("Zoom", "authenticate", e)
            params: dict = {"page_size": limit, "type": "previous_meetings"}
            if from_date:
                params["from"] = from_date
            if to_date:
                params["to"] = to_date
            r, err = await token_store.safe_request(
                client, "GET", f"{API}/users/me/meetings",
                service="Zoom", action="list past meetings",
                headers={"Authorization": f"Bearer {token}"},
                params=params,
            )
            if err:
                return err
            meetings = r.json().get("meetings", [])
            if not meetings:
                return "No past meetings found."
            return "\n\n".join(
                f"{m.get('topic', 'Untitled')}\n  ID: {m['id']} | Start: {m.get('start_time', '?')} | Duration: {m.get('duration', '?')} min"
                for m in meetings
            )

        @mcp.tool()
        async def zoom_get_recording_transcript(meeting_id: str, ctx: Context) -> str:
            """Get the VTT transcript for a Zoom meeting recording.

            Args:
                meeting_id: The Zoom meeting ID
            """
            err = validation.validate_id(meeting_id, "meeting_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "zoom", level="read")
            if err:
                return err
            creds = token_store.get_credentials(uid, "zoom")
            try:
                token = await _get_access_token(creds)
            except (httpx.HTTPStatusError, httpx.RequestError, ValueError) as e:
                return token_store.format_api_error("Zoom", "authenticate", e)
            encoded_id = urllib.parse.quote(meeting_id, safe="")
            r, err = await token_store.safe_request(
                client, "GET", f"{API}/meetings/{encoded_id}/recordings",
                service="Zoom", action="get recording transcript",
                headers={"Authorization": f"Bearer {token}"},
            )
            if err:
                return err
            files = r.json().get("recording_files", [])
            for f in files:
                if f.get("file_type") == "TRANSCRIPT" or f.get("file_extension") == "VTT":
                    return f"Transcript download URL: {f.get('download_url', 'N/A')}"
            return "No VTT transcript found for this meeting."

        @mcp.tool()
        async def zoom_create_meeting(ctx: Context, topic: str, start_time: str, duration: int = 60, timezone: str = "UTC", agenda: str = "") -> str:
            """Schedule a new Zoom meeting.

            Args:
                topic: Meeting topic/title
                start_time: Start time in ISO 8601 format (e.g. 2024-06-01T10:00:00Z)
                duration: Duration in minutes (default: 60)
                timezone: Timezone (default: UTC)
                agenda: Meeting agenda (optional)
            """
            err = validation.validate_content(topic, "topic")
            if err:
                return err
            err = validation.validate_content(start_time, "start_time")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "zoom", level="write")
            if err:
                return err
            creds = token_store.get_credentials(uid, "zoom")
            try:
                token = await _get_access_token(creds)
            except (httpx.HTTPStatusError, httpx.RequestError, ValueError) as e:
                return token_store.format_api_error("Zoom", "authenticate", e)
            body: dict = {
                "topic": topic,
                "type": 2,
                "start_time": start_time,
                "duration": duration,
                "timezone": timezone,
            }
            if agenda:
                body["agenda"] = agenda
            r, err = await token_store.safe_request(
                client, "POST", f"{API}/users/me/meetings",
                service="Zoom", action="create meeting",
                headers={"Authorization": f"Bearer {token}"},
                json=body,
            )
            if err:
                return err
            m = r.json()
            return (
                f"Meeting created successfully.\n"
                f"Topic: {m.get('topic', '?')}\n"
                f"ID: {m.get('id', '?')}\n"
                f"Start: {m.get('start_time', '?')}\n"
                f"Duration: {m.get('duration', '?')} min\n"
                f"Join URL: {m.get('join_url', '?')}"
            )

        @mcp.tool()
        async def zoom_update_meeting(meeting_id: int, ctx: Context, topic: str = "", start_time: str = "", duration: int = 0) -> str:
            """Update an existing Zoom meeting.

            Args:
                meeting_id: The Zoom meeting ID
                topic: New topic (optional)
                start_time: New start time in ISO 8601 format (optional)
                duration: New duration in minutes (optional, 0 = no change)
            """
            client, uid, err = await token_store.require_service(ctx, "zoom", level="write")
            if err:
                return err
            creds = token_store.get_credentials(uid, "zoom")
            try:
                token = await _get_access_token(creds)
            except (httpx.HTTPStatusError, httpx.RequestError, ValueError) as e:
                return token_store.format_api_error("Zoom", "authenticate", e)
            body: dict = {}
            if topic:
                body["topic"] = topic
            if start_time:
                body["start_time"] = start_time
            if duration:
                body["duration"] = duration
            if not body:
                return "No fields to update. Provide at least one of: topic, start_time, duration."
            r, err = await token_store.safe_request(
                client, "PATCH", f"{API}/meetings/{meeting_id}",
                service="Zoom", action="update meeting",
                headers={"Authorization": f"Bearer {token}"},
                json=body,
            )
            if err:
                return err
            return f"Meeting {meeting_id} updated successfully."

        @mcp.tool()
        async def zoom_delete_meeting(meeting_id: int, ctx: Context) -> str:
            """Cancel/delete a Zoom meeting.

            Args:
                meeting_id: The Zoom meeting ID
            """
            client, uid, err = await token_store.require_service(ctx, "zoom", level="write")
            if err:
                return err
            creds = token_store.get_credentials(uid, "zoom")
            try:
                token = await _get_access_token(creds)
            except (httpx.HTTPStatusError, httpx.RequestError, ValueError) as e:
                return token_store.format_api_error("Zoom", "authenticate", e)
            r, err = await token_store.safe_request(
                client, "DELETE", f"{API}/meetings/{meeting_id}",
                service="Zoom", action="delete meeting",
                headers={"Authorization": f"Bearer {token}"},
            )
            if err:
                return err
            return f"Meeting {meeting_id} deleted successfully."

        @mcp.tool()
        async def zoom_get_meeting_registrants(meeting_id: int, ctx: Context) -> str:
            """List registrants for a Zoom meeting.

            Args:
                meeting_id: The Zoom meeting ID
            """
            client, uid, err = await token_store.require_service(ctx, "zoom", level="read")
            if err:
                return err
            creds = token_store.get_credentials(uid, "zoom")
            try:
                token = await _get_access_token(creds)
            except (httpx.HTTPStatusError, httpx.RequestError, ValueError) as e:
                return token_store.format_api_error("Zoom", "authenticate", e)
            r, err = await token_store.safe_request(
                client, "GET", f"{API}/meetings/{meeting_id}/registrants",
                service="Zoom", action="list registrants",
                headers={"Authorization": f"Bearer {token}"},
            )
            if err:
                return err
            registrants = r.json().get("registrants", [])
            if not registrants:
                return "No registrants found."
            return "\n".join(
                f"{reg.get('first_name', '')} {reg.get('last_name', '')} | Email: {reg.get('email', '?')} | Status: {reg.get('status', '?')}"
                for reg in registrants
            )

        @mcp.tool()
        async def zoom_list_users(ctx: Context, status: str = "active", limit: int = 30) -> str:
            """List users in the Zoom account.

            Args:
                status: User status filter (active, inactive, pending). Default: active.
                limit: Max results (default: 30)
            """
            limit = validation.validate_limit(limit)
            client, uid, err = await token_store.require_service(ctx, "zoom", level="read")
            if err:
                return err
            creds = token_store.get_credentials(uid, "zoom")
            try:
                token = await _get_access_token(creds)
            except (httpx.HTTPStatusError, httpx.RequestError, ValueError) as e:
                return token_store.format_api_error("Zoom", "authenticate", e)
            r, err = await token_store.safe_request(
                client, "GET", f"{API}/users",
                service="Zoom", action="list users",
                headers={"Authorization": f"Bearer {token}"},
                params={"status": status, "page_size": min(limit, 100)},
            )
            if err:
                return err
            users = r.json().get("users", [])
            if not users:
                return "No users found."
            return "\n".join(
                f"{u.get('first_name', '')} {u.get('last_name', '')} | Email: {u.get('email', '?')} | Type: {u.get('type', '?')} | Status: {u.get('status', '?')}"
                for u in users
            )

        @mcp.tool()
        async def zoom_get_user(user_id: str, ctx: Context) -> str:
            """Get details of a Zoom user.

            Args:
                user_id: The user ID or email address
            """
            err = validation.validate_id(user_id, "user_id")
            if err:
                return err
            client, uid, err = await token_store.require_service(ctx, "zoom", level="read")
            if err:
                return err
            creds = token_store.get_credentials(uid, "zoom")
            try:
                token = await _get_access_token(creds)
            except (httpx.HTTPStatusError, httpx.RequestError, ValueError) as e:
                return token_store.format_api_error("Zoom", "authenticate", e)
            encoded_id = urllib.parse.quote(user_id, safe="")
            r, err = await token_store.safe_request(
                client, "GET", f"{API}/users/{encoded_id}",
                service="Zoom", action="get user",
                headers={"Authorization": f"Bearer {token}"},
            )
            if err:
                return err
            u = r.json()
            return (
                f"{u.get('first_name', '')} {u.get('last_name', '')}\n"
                f"Email: {u.get('email', '?')}\n"
                f"Type: {u.get('type', '?')}\n"
                f"Status: {u.get('status', '?')}\n"
                f"PMI: {u.get('pmi', '?')}\n"
                f"Timezone: {u.get('timezone', '?')}"
            )
