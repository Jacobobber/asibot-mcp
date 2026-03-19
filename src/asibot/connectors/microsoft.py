"""Shared Microsoft Graph API auth. One token covers all MS365 services.

Token stored per-user at ~/.asibot/users/{user_id}/microsoft_token.json (encrypted)
Used by: sharepoint, outlook, teams connectors.
"""

import asyncio
import logging
import time

import httpx

from asibot import user_session
from asibot.config import settings
from asibot.crypto import load_encrypted, save_encrypted

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# Read-only MS365 scopes (List 1)
SCOPES = (
    "User.Read "
    "GroupMember.Read.All "  # Azure AD group membership for role sync
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

_user_clients: dict[str, httpx.AsyncClient] = {}
_client_lock = asyncio.Lock()


async def close_all_clients() -> None:
    """Close all cached HTTP clients. Call on server shutdown."""
    for uid, client in list(_user_clients.items()):
        try:
            await client.aclose()
        except Exception:
            logger.warning("Failed to close client for user '%s'", uid)
    _user_clients.clear()


async def close_client(user_id: str) -> None:
    """Close and remove the cached client for a specific user."""
    client = _user_clients.pop(user_id, None)
    if client:
        try:
            await client.aclose()
        except Exception:
            logger.warning("Failed to close client for user '%s'", user_id)


def token_path(user_id: str):
    return user_session.get_user_data_dir(user_id) / "microsoft_token.json"


def load_token(user_id: str) -> dict:
    return load_encrypted(token_path(user_id))


def save_token(user_id: str, token_data: dict) -> None:
    save_encrypted(token_path(user_id), token_data)


def is_expired(token_data: dict) -> bool:
    return time.time() > (token_data.get("expires_at", 0) - 300)


async def refresh_token(user_id: str, token_data: dict) -> bool:
    tenant_id = settings.ms365_tenant_id
    client_id = settings.ms365_client_id
    rt = token_data.get("refresh_token")
    if not rt:
        logger.warning("Microsoft: no refresh_token for user '%s'", user_id)
        return False

    try:
        async with httpx.AsyncClient() as http:
            resp = await http.post(
                f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "refresh_token": rt,
                    "scope": SCOPES,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        new_token = {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token", rt),
            "expires_at": time.time() + data.get("expires_in", 3600),
        }
        save_token(user_id, new_token)
        logger.info("Microsoft: refreshed token for user '%s'", user_id)
        # Update cached client only after successful save
        if user_id in _user_clients:
            _user_clients[user_id].headers["Authorization"] = f"Bearer {new_token['access_token']}"
        return True
    except httpx.HTTPStatusError:
        logger.exception("Microsoft: token refresh HTTP error for user '%s'", user_id)
        # Invalidate cached client on auth failure
        _user_clients.pop(user_id, None)
        return False
    except (httpx.RequestError, KeyError, ValueError):
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


async def get_client(user_id: str) -> httpx.AsyncClient | None:
    """Get an authenticated httpx client for this user's Microsoft Graph calls."""
    token_data = load_token(user_id)
    if not token_data.get("access_token"):
        return None

    async with _client_lock:
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
    client = await get_client(uid)
    if not client:
        return None, None, "Could not create Microsoft Graph client."
    return client, uid, None
