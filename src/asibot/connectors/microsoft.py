"""Shared Microsoft Graph API auth. One token covers all MS365 services.

Token stored per-user at ~/.asibot/users/{user_id}/microsoft_token.json
Used by: sharepoint, outlook, teams connectors.
"""

import json
import logging
import time

import httpx

from asibot import user_session
from asibot.config import settings

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# Read-only MS365 scopes (List 1)
SCOPES = (
    "User.Read "
    "Sites.Read.All "
    "Files.Read.All "
    "Mail.Read "
    "Calendars.Read "
    "Team.ReadBasic.All "
    "ChannelMessage.Read.All "
    "Chat.Read "
    "Notes.Read.All "
    "Tasks.Read "
    "offline_access"
)

# Write/agentic scopes (List 2 — add when admin approves)
# SCOPES_WRITE = (
#     "Mail.Send "
#     "Mail.ReadWrite "
#     "Calendars.ReadWrite "
#     "Files.ReadWrite.All "
#     "ChannelMessage.Send "
#     "Chat.ReadWrite "
#     "Tasks.ReadWrite "
#     "Notes.ReadWrite.All"
# )

_user_clients: dict[str, httpx.AsyncClient] = {}


def token_path(user_id: str):
    return user_session.get_user_data_dir(user_id) / "microsoft_token.json"


def load_token(user_id: str) -> dict:
    path = token_path(user_id)
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}
    return {}


def save_token(user_id: str, token_data: dict) -> None:
    path = token_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(token_data))


def is_expired(token_data: dict) -> bool:
    return time.time() > (token_data.get("expires_at", 0) - 300)


async def refresh_token(user_id: str, token_data: dict) -> bool:
    tenant_id = settings.sharepoint_tenant_id
    client_id = settings.sharepoint_client_id

    try:
        async with httpx.AsyncClient() as http:
            resp = await http.post(
                f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "refresh_token": token_data["refresh_token"],
                    "scope": SCOPES,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        new_token = {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token", token_data["refresh_token"]),
            "expires_at": time.time() + data.get("expires_in", 3600),
        }
        save_token(user_id, new_token)
        # Update client if cached
        if user_id in _user_clients:
            _user_clients[user_id].headers["Authorization"] = f"Bearer {new_token['access_token']}"
        logger.info("Microsoft: refreshed token for user '%s'", user_id)
        return True
    except Exception:
        logger.exception("Microsoft: token refresh failed for user '%s'", user_id)
        return False


async def ensure_auth(user_id: str) -> bool:
    """Check if user has a valid Microsoft token. Auto-refreshes if needed."""
    token_data = load_token(user_id)
    if token_data.get("access_token") and not is_expired(token_data):
        return True
    if token_data.get("refresh_token"):
        return await refresh_token(user_id, token_data)
    return False


def get_client(user_id: str) -> httpx.AsyncClient | None:
    """Get an authenticated httpx client for this user's Microsoft Graph calls."""
    token_data = load_token(user_id)
    if not token_data.get("access_token"):
        return None

    client = _user_clients.get(user_id)
    if client is None:
        client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {token_data['access_token']}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
        _user_clients[user_id] = client
    else:
        client.headers["Authorization"] = f"Bearer {token_data['access_token']}"
    return client


async def require_graph_client(ctx, service: str = "sharepoint", level: str = "read") -> tuple[httpx.AsyncClient | None, str | None, str | None]:
    """Common auth + permission check for all MS365 tools.

    Args:
        ctx: MCP Context
        service: Microsoft service name (sharepoint, outlook, calendar, teams)
        level: "read" or "write"

    Returns (client, user_id, error_message).
    """
    from asibot import token_store

    uid, err = token_store.check_permission(ctx, service, level)
    if err:
        return None, None, err
    if not await ensure_auth(uid):
        return None, None, "Microsoft 365 not authenticated. Run asibot_setup to sign in."
    client = get_client(uid)
    if not client:
        return None, None, "Could not create Microsoft Graph client."
    return client, uid, None
