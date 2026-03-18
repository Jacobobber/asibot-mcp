"""Async SQLite connection pool and session persistence.

Provides an ``AsyncConnectionPool`` for aiosqlite that avoids serialising all
DB operations through a single connection, plus helpers for persisting user
sessions across server restarts.
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

import aiosqlite

from asibot.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Connection pool
# ---------------------------------------------------------------------------


class AsyncConnectionPool:
    """Simple async connection pool for aiosqlite.

    Parameters
    ----------
    db_path : str
        Path to the SQLite database file.
    pool_size : int
        Maximum number of concurrent connections (default from config).
    busy_timeout : int
        SQLite busy-timeout in milliseconds.
    """

    def __init__(
        self,
        db_path: str,
        pool_size: int | None = None,
        busy_timeout: int = 30_000,
    ) -> None:
        self._db_path = db_path
        self._pool_size = pool_size or settings.db_pool_size
        self._busy_timeout = busy_timeout
        self._semaphore = asyncio.Semaphore(self._pool_size)
        self._connections: asyncio.Queue[aiosqlite.Connection] = asyncio.Queue()
        self._active: int = 0
        self._total_created: int = 0
        self._waiting: int = 0
        self._closed = False
        self._lock = asyncio.Lock()

    # -- metrics --

    @property
    def active_connections(self) -> int:
        """Number of connections currently checked out."""
        return self._active

    @property
    def waiting_tasks(self) -> int:
        """Number of tasks waiting for a connection."""
        return self._waiting

    @property
    def pool_size(self) -> int:
        return self._pool_size

    # -- internals --

    async def _create_connection(self) -> aiosqlite.Connection:
        """Create a new connection with WAL mode and busy_timeout."""
        conn = await aiosqlite.connect(self._db_path)
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute(f"PRAGMA busy_timeout={self._busy_timeout}")
        self._total_created += 1
        logger.debug("Created new DB connection (#%d)", self._total_created)
        return conn

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[aiosqlite.Connection]:
        """Acquire a connection from the pool.

        Usage::

            async with pool.acquire() as conn:
                await conn.execute(...)
        """
        if self._closed:
            raise RuntimeError("Connection pool is closed")

        self._waiting += 1
        await self._semaphore.acquire()
        self._waiting -= 1

        conn: aiosqlite.Connection | None = None
        try:
            # Try to reuse an idle connection
            if not self._connections.empty():
                conn = self._connections.get_nowait()
                # Verify connection is still usable
                try:
                    await conn.execute("SELECT 1")
                except Exception:
                    logger.debug("Stale pooled connection discarded")
                    try:
                        await conn.close()
                    except Exception:
                        pass
                    conn = None

            if conn is None:
                conn = await self._create_connection()

            self._active += 1
            yield conn

        except Exception:
            # On error, discard the connection rather than returning it
            if conn is not None:
                try:
                    await conn.close()
                except Exception:
                    pass
                conn = None
            raise
        finally:
            self._active -= 1
            if conn is not None and not self._closed:
                await self._connections.put(conn)
            elif conn is not None:
                try:
                    await conn.close()
                except Exception:
                    pass
            self._semaphore.release()

    async def close(self) -> None:
        """Close all pooled connections."""
        self._closed = True
        while not self._connections.empty():
            try:
                conn = self._connections.get_nowait()
                await conn.close()
            except Exception:
                pass
        logger.info("Connection pool closed")


# ---------------------------------------------------------------------------
# Global pool (initialised lazily)
# ---------------------------------------------------------------------------

_pool: AsyncConnectionPool | None = None
_pool_lock = asyncio.Lock()


async def get_pool() -> AsyncConnectionPool:
    """Return the global connection pool, creating it on first call."""
    global _pool
    if _pool is not None:
        return _pool
    async with _pool_lock:
        if _pool is not None:
            return _pool
        db_path = str(settings.data_dir / "asibot.db")
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        _pool = AsyncConnectionPool(db_path)
        # Ensure the sessions table exists
        async with _pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id    TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                )
                """
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at)"
            )
            await conn.commit()
        logger.info("DB pool initialised (pool_size=%d, path=%s)", _pool.pool_size, db_path)
        return _pool


async def close_pool() -> None:
    """Shut down the global pool (call on server shutdown)."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


# ---------------------------------------------------------------------------
# Session persistence helpers
# ---------------------------------------------------------------------------


async def cache_session(session_id: str, user_id: str, ttl: int | None = None) -> None:
    """Persist a session to the database."""
    if ttl is None:
        ttl = settings.session_ttl
    now = time.time()
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT OR REPLACE INTO sessions (session_id, user_id, created_at, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            (session_id, user_id, now, now + ttl),
        )
        await conn.commit()


async def get_session_user(session_id: str) -> str | None:
    """Look up a session in the database, returning the user_id if valid."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        cursor = await conn.execute(
            "SELECT user_id FROM sessions WHERE session_id = ? AND expires_at > ?",
            (session_id, time.time()),
        )
        row = await cursor.fetchone()
        return row[0] if row else None


async def load_active_sessions(ttl: int | None = None) -> dict[str, tuple[str, float]]:
    """Load all non-expired sessions from the database.

    Returns a dict of ``{session_id: (user_id, created_at)}``.
    """
    pool = await get_pool()
    now = time.time()
    async with pool.acquire() as conn:
        cursor = await conn.execute(
            "SELECT session_id, user_id, created_at FROM sessions WHERE expires_at > ?",
            (now,),
        )
        rows = await cursor.fetchall()
    return {row[0]: (row[1], row[2]) for row in rows}


async def delete_user_sessions(user_id: str) -> int:
    """Remove all sessions for a user from the database.

    Returns the number of rows deleted.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        cursor = await conn.execute(
            "DELETE FROM sessions WHERE user_id = ?",
            (user_id,),
        )
        await conn.commit()
        return cursor.rowcount


async def purge_expired_sessions() -> int:
    """Delete expired sessions from the database.

    Returns the number of rows deleted.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        cursor = await conn.execute(
            "DELETE FROM sessions WHERE expires_at <= ?",
            (time.time(),),
        )
        await conn.commit()
        return cursor.rowcount
