"""Per-user session management via API key auth.

Auth flow:
1. New user calls asibot_setup() (no auth required) → SSO → gets API key
2. User adds API key to Claude Desktop config as Authorization header
3. Every request carries the API key → server resolves user identity automatically
"""

import logging
from pathlib import Path

from mcp.server.fastmcp import Context

from asibot import auth
from asibot.config import settings

logger = logging.getLogger(__name__)

_session_to_user: dict[str, str] = {}  # session_id -> user_id (cache)


def get_session_id(ctx: Context) -> str | None:
    """Extract MCP session ID from Context."""
    try:
        request = ctx.request_context.request
        if request is not None and hasattr(request, "headers"):
            return request.headers.get("mcp-session-id")
    except (AttributeError, TypeError):
        pass
    return None


def _get_api_key(ctx: Context) -> str | None:
    """Extract API key from Authorization header."""
    try:
        request = ctx.request_context.request
        if request is not None and hasattr(request, "headers"):
            auth_header = request.headers.get("authorization", "")
            if auth_header.startswith("Bearer "):
                return auth_header[7:]
    except (AttributeError, TypeError):
        pass
    return None


def get_user_data_dir(user_id: str) -> Path:
    user_dir = settings.data_dir / "users" / user_id.replace("@", "_at_")
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir


def require_user(ctx: Context) -> tuple[str | None, str | None]:
    """Resolve user from API key or session cache.

    Returns (user_id, error_message).
    """
    session_id = get_session_id(ctx)

    # Check session cache first
    if session_id and session_id in _session_to_user:
        return _session_to_user[session_id], None

    # Try API key auth
    api_key = _get_api_key(ctx)
    if api_key:
        user = auth.get_user_by_key(api_key)
        if user:
            user_id = user["user_id"]
            if session_id:
                _session_to_user[session_id] = user_id
            return user_id, None
        return None, "Invalid API key. Run asibot_setup to get a valid key."

    # No API key — check if there's a single registered user (convenience for local dev)
    users = auth.list_users()
    if len(users) == 1:
        user_id = users[0]["user_id"]
        if session_id:
            _session_to_user[session_id] = user_id
        return user_id, None

    if len(users) == 0:
        return None, "No users set up yet. Use asibot_setup to create your account."

    return None, "Authentication required. Add your API key to your Claude Desktop config as an Authorization header."
