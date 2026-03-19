"""Per-user session management via API key auth.

Auth flow:
1. New user calls asibot_setup() (no auth required) -> SSO -> gets API key
2. User adds API key to Claude Desktop config as Authorization header
3. Every request carries the API key -> server resolves user identity automatically


Sessions use a pluggable backend (in-memory or Redis) via the SessionStore
abstraction.  They are also persisted to SQLite so they survive server restarts.
DB writes happen asynchronously via background tasks.  On startup, active
sessions are loaded from the DB into the session store.
"""

import asyncio
import logging
import re
import sqlite3
import time
from collections import OrderedDict
from pathlib import Path

from mcp.server.fastmcp import Context

from asibot import auth
from asibot.config import settings
from asibot.session_store import SessionStore, create_session_store

logger = logging.getLogger(__name__)

_MAX_SESSIONS = 10_000



# Rate limiting for failed auth attempts -- per API key prefix
_AUTH_FAIL_WINDOW = 300  # 5 minutes
_AUTH_FAIL_MAX = 10  # max failures per window before lockout

# Only allow email-like user IDs: alphanumeric, dots, hyphens, underscores, @
_SAFE_USER_ID = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")

# Lazily initialised session store (created on first use to avoid import-time
# side effects when Redis is configured).
_store: SessionStore | None = None


def _get_store() -> SessionStore:
    """Return the module-level session store, creating it on first access."""
    global _store
    if _store is None:
        _store = create_session_store()
    return _store


def set_store(store: SessionStore) -> None:
    """Replace the module-level session store (used by tests and startup hooks)."""
    global _store
    _store = store


def _sanitize_user_id(user_id: str) -> str:
    """Validate and sanitize user_id to prevent path traversal."""
    if not _SAFE_USER_ID.match(user_id):
        raise ValueError(f"Invalid user ID format: {user_id}")
    # Extra safety: reject any path traversal attempts
    sanitized = user_id.replace("@", "_at_")
    if ".." in sanitized or "/" in sanitized or "\\" in sanitized:
        raise ValueError(f"Invalid user ID format: {user_id}")
    return sanitized


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




def _key_prefix(api_key: str) -> str:
    """Derive a rate-limit bucket key from the first 8 chars of the API key."""
    return api_key[:8] if api_key else "__nokey__"


def _record_auth_failure(key_pfx: str) -> None:
    """Record a failed auth attempt for rate limiting."""
    _get_store().record_auth_failure(key_pfx)


def _is_rate_limited(key_pfx: str) -> bool:
    """Check if auth attempts for this key prefix are rate-limited."""
    return _get_store().is_rate_limited(key_pfx, _AUTH_FAIL_WINDOW, _AUTH_FAIL_MAX)


# ---------------------------------------------------------------------------
# DB persistence helpers (sync sqlite3 for the hot read path)
# ---------------------------------------------------------------------------

def _db_path() -> str:
    """Return the path to the sessions database."""
    return str(settings.data_dir / "asibot.db")


def _db_lookup_session(session_id: str) -> str | None:
    """Synchronous DB lookup for a single session (fallback when not in memory).

    Uses plain sqlite3 so it can be called from synchronous code.
    """
    try:
        conn = sqlite3.connect(_db_path(), timeout=5)
        try:
            row = conn.execute(
                "SELECT user_id FROM sessions WHERE session_id = ? AND expires_at > ?",
                (session_id, time.time()),
            ).fetchone()
            return row[0] if row else None
        finally:
            conn.close()
    except Exception:
        logger.debug("DB session lookup failed", exc_info=True)
        return None


def _schedule_db_write(session_id: str, user_id: str) -> None:
    """Schedule an async DB write for session persistence (fire-and-forget)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no event loop -- skip DB persistence (e.g., in tests)
    loop.create_task(_async_db_cache_session(session_id, user_id))


async def _async_db_cache_session(session_id: str, user_id: str) -> None:
    """Persist a session to the database asynchronously."""
    try:
        from asibot.db import cache_session
        await cache_session(session_id, user_id)
    except Exception:
        logger.debug("Async DB session write failed", exc_info=True)


def _schedule_db_delete_user(user_id: str) -> None:
    """Schedule async deletion of all sessions for a user from the DB."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(_async_db_delete_user(user_id))


async def _async_db_delete_user(user_id: str) -> None:
    """Delete all sessions for a user from the database asynchronously."""
    try:
        from asibot.db import delete_user_sessions
        await delete_user_sessions(user_id)
    except Exception:
        logger.debug("Async DB session delete failed", exc_info=True)


# ---------------------------------------------------------------------------
# In-memory cache operations
# ---------------------------------------------------------------------------


def _cache_session(session_id: str, user_id: str) -> None:
    """Add a session to the cache, delegating to the configured store.

    Also persists to the SQLite DB asynchronously.
    """
    _get_store().put_session(session_id, user_id, settings.session_ttl)

    # Persist to DB (fire-and-forget)
    _schedule_db_write(session_id, user_id)


def invalidate_user_sessions(user_id: str) -> int:
    """Remove all cached sessions for a specific user (e.g., after key rotation).

    Clears both in-memory cache and database.
    Returns the number of in-memory sessions invalidated.
    """
    count = _get_store().delete_user_sessions(user_id)
    if count:
        logger.info("Invalidated %d session(s) for user '%s'", count, user_id)

    # Also clear from DB (fire-and-forget)
    _schedule_db_delete_user(user_id)

    return count


def get_user_data_dir(user_id: str) -> Path:
    safe_name = _sanitize_user_id(user_id)
    user_dir = settings.data_dir / "users" / safe_name
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir


async def require_user(ctx: Context) -> tuple[str | None, str | None]:
    """Resolve user from API key or session cache.

    Checks the in-memory cache first, then falls back to the database.
    Returns (user_id, error_message).
    """
    session_id = get_session_id(ctx)
    store = _get_store()

    # Check session store first (supports in-memory LRU or Redis)
    if session_id:
        result = store.get_session(session_id)
        if result is not None:
            user_id, _ts = result
            return user_id, None

    # Fallback: check database for sessions not in the store (e.g., after eviction/restart)
    if session_id:
        db_user = _db_lookup_session(session_id)
        if db_user:
            # Re-populate the session store
            store.put_session(session_id, db_user, settings.session_ttl)
            logger.debug("Session %s restored from DB for user '%s'", session_id[:8], db_user)
            return db_user, None

    # Try API key auth
    api_key = _get_api_key(ctx)
    if api_key:
        key_pfx = _key_prefix(api_key)
        # Rate limit check per key prefix before attempting auth
        if _is_rate_limited(key_pfx):
            logger.warning("Auth rate limit exceeded for key prefix %s", key_pfx)
            return None, "Too many failed authentication attempts. Try again in a few minutes."
        user = await auth.get_user_by_key(api_key)
        if user:
            user_id = user["user_id"]
            if session_id:
                _cache_session(session_id, user_id)
            return user_id, None
        _record_auth_failure(key_pfx)
        return None, "Invalid API key. Run asibot_setup to get a valid key."

    # No API key -- only auto-login single user on stdio transport (local dev)
    if settings.transport == "stdio":
        users = await auth.list_users()
        if len(users) == 1:
            user_id = users[0]["user_id"]
            if session_id:
                _cache_session(session_id, user_id)
            return user_id, None

    users = await auth.list_users()
    if len(users) == 0:
        return None, "No users set up yet. Use asibot_setup to create your account."

    return None, "Authentication required. Add your API key to your Claude Desktop config as an Authorization header."


# ---------------------------------------------------------------------------
# Startup: load persisted sessions into memory
# ---------------------------------------------------------------------------


async def load_sessions_from_db() -> int:
    """Load active (non-expired) sessions from the database into the memory cache.

    Call this during server startup so that sessions survive restarts.
    Returns the number of sessions loaded.
    """
    try:
        from asibot.db import load_active_sessions
        sessions = await load_active_sessions()
    except Exception:
        logger.warning("Failed to load sessions from DB", exc_info=True)
        return 0

    store = _get_store()
    count = 0
    for session_id, (user_id, created_at) in sessions.items():
        if count >= _MAX_SESSIONS:
            break
        store.put_session(session_id, user_id, settings.session_ttl)
        count += 1

    if count:
        logger.info("Loaded %d active session(s) from database", count)
    return count
