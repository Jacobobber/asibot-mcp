"""Database facade — delegates all operations to the PostgreSQL backend.

All callers import from this module (e.g., ``db.create_user()``, ``db.log_audit()``).
The underlying backend is ``db_postgres.PostgresBackend``.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

import asyncpg

from asibot.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Backend singleton
# ---------------------------------------------------------------------------

_backend = None
_backend_lock = asyncio.Lock()


async def _get_backend():
    """Return the initialised backend, creating it on first call."""
    global _backend
    if _backend is not None:
        return _backend
    async with _backend_lock:
        if _backend is not None:
            return _backend
        from asibot.db_postgres import PostgresBackend
        _backend = PostgresBackend(
            settings.database_url,
            min_size=settings.pg_pool_min_size,
            max_size=settings.pg_pool_max_size,
        )
        await _backend.initialize()
        logger.info("Database backend initialised (PostgreSQL)")
        return _backend


async def init_db(db_path=None) -> None:
    """Initialise the database (idempotent)."""
    await _get_backend()


async def close_db() -> None:
    """Close the database connection."""
    global _backend
    if _backend is not None:
        await _backend.close()
        _backend = None
        logger.info("Database connection closed")


async def close_pool() -> None:
    """Alias for close_db (backwards compat)."""
    await close_db()


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


async def create_user(user_id: str, name: str, api_key: str, created_at: str, role: str = "user") -> bool:
    return await (await _get_backend()).create_user(user_id, name, api_key, created_at, role)


async def get_user_by_key(api_key: str) -> dict | None:
    return await (await _get_backend()).get_user_by_key(api_key)


async def get_user_by_email(email: str) -> dict | None:
    return await (await _get_backend()).get_user_by_email(email)


async def rotate_key(email: str, new_key: str, rotated_at: str) -> dict | None:
    return await (await _get_backend()).rotate_key(email, new_key, rotated_at)


async def list_users() -> list[dict]:
    return await (await _get_backend()).list_users()


async def set_role(email: str, role: str) -> dict | None:
    return await (await _get_backend()).set_role(email, role)


async def set_role_with_audit(
    email: str,
    role: str,
    *,
    admin_id: str | None = None,
) -> dict | None:
    return await (await _get_backend()).set_role_with_audit(
        email, role, admin_id=admin_id,
    )


@asynccontextmanager
async def transaction() -> AsyncIterator[asyncpg.Connection]:
    """Expose the backend's transaction context manager through the facade."""
    backend = await _get_backend()
    async with backend.transaction() as conn:
        yield conn


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


async def set_credentials(user_id: str, service: str, encrypted_blob: bytes) -> None:
    await (await _get_backend()).set_credentials(user_id, service, encrypted_blob)


async def get_credentials(user_id: str, service: str) -> bytes | None:
    return await (await _get_backend()).get_credentials(user_id, service)


async def remove_credentials(user_id: str, service: str) -> None:
    await (await _get_backend()).remove_credentials(user_id, service)


async def list_connected(user_id: str) -> list[str]:
    return await (await _get_backend()).list_connected(user_id)


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------


async def get_service_prefs(user_id: str, service: str) -> dict:
    return await (await _get_backend()).get_service_prefs(user_id, service)


async def set_service_prefs(user_id: str, service: str, enabled: bool, mode: str) -> None:
    await (await _get_backend()).set_service_prefs(user_id, service, enabled, mode)


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


async def cache_session(session_id: str, user_id: str, ttl: int | None = None) -> None:
    if ttl is None:
        ttl = settings.session_ttl
    await (await _get_backend()).cache_session(session_id, user_id, ttl)


async def get_session_user(session_id: str) -> str | None:
    return await (await _get_backend()).get_session_user(session_id)


async def load_active_sessions(ttl: int | None = None) -> dict[str, tuple[str, float]]:
    return await (await _get_backend()).load_active_sessions()


async def delete_user_sessions(user_id: str) -> int:
    return await (await _get_backend()).delete_user_sessions(user_id)


async def purge_expired_sessions() -> int:
    return await (await _get_backend()).purge_expired_sessions()


# Alias used by server.py
cleanup_expired_sessions = purge_expired_sessions


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


async def log_audit(
    user_id: str | None,
    tool: str,
    args_json: str | None = None,
    *,
    service: str | None = None,
    success: bool | None = None,
    latency_ms: float | None = None,
    error_type: str | None = None,
) -> None:
    await (await _get_backend()).log_audit(
        user_id, tool, args_json,
        service=service, success=success,
        latency_ms=latency_ms, error_type=error_type,
    )


async def log_event(
    user_id: str | None,
    event: str,
    *,
    service: str | None = None,
    metadata: dict | None = None,
) -> None:
    await (await _get_backend()).log_event(
        user_id, event, service=service, metadata=metadata,
    )


async def query_audit(
    *,
    user_id: str | None = None,
    tool: str | None = None,
    since: float | None = None,
    limit: int = 100,
) -> list[dict]:
    return await (await _get_backend()).query_audit(
        user_id=user_id, tool=tool, since=since, limit=limit,
    )


async def query_audit_range(start: float, end: float) -> list[dict]:
    return await (await _get_backend()).query_audit_range(start, end)


async def prune_audit(max_age_days: int = 90) -> int:
    return await (await _get_backend()).prune_audit(max_age_days)


# ---------------------------------------------------------------------------
# Microsoft Tokens
# ---------------------------------------------------------------------------


async def save_ms_token(user_id: str, encrypted_blob: bytes) -> None:
    await (await _get_backend()).save_ms_token(user_id, encrypted_blob)


async def load_ms_token(user_id: str) -> bytes | None:
    return await (await _get_backend()).load_ms_token(user_id)


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


async def db_stats() -> dict:
    return await (await _get_backend()).db_stats()


async def set_credentials_with_prefs(
    user_id: str,
    service: str,
    encrypted_blob: bytes,
    enabled: bool = True,
    mode: str = "read",
) -> None:
    await (await _get_backend()).set_credentials_with_prefs(
        user_id, service, encrypted_blob, enabled, mode,
    )
