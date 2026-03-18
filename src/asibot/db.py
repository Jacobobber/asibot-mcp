"""Async SQLite connection pool and session persistence.

Provides an ``AsyncConnectionPool`` for aiosqlite that avoids serialising all
DB operations through a single connection, plus helpers for persisting user
sessions across server restarts.
"""

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
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


# ---------------------------------------------------------------------------
# Legacy singleton connection (used by users, credentials, audit, etc.)
# ---------------------------------------------------------------------------

_db: aiosqlite.Connection | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id       TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    api_key       TEXT UNIQUE NOT NULL,
    created_at    TEXT NOT NULL,
    key_rotated_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_api_key ON users(api_key);

CREATE TABLE IF NOT EXISTS credentials (
    user_id       TEXT NOT NULL,
    service       TEXT NOT NULL,
    encrypted_blob BLOB NOT NULL,
    updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, service)
);

CREATE TABLE IF NOT EXISTS preferences (
    user_id       TEXT NOT NULL,
    service       TEXT NOT NULL,
    enabled       INTEGER NOT NULL DEFAULT 1,
    mode          TEXT NOT NULL DEFAULT 'read',
    updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, service)
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id    TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    last_used_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS audit_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            REAL NOT NULL,
    user_id       TEXT,
    tool          TEXT,
    args_json     TEXT,
    service       TEXT,
    success       INTEGER,
    latency_ms    REAL,
    error_type    TEXT,
    event         TEXT,
    metadata_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_service ON audit_log(service);

CREATE TABLE IF NOT EXISTS microsoft_tokens (
    user_id        TEXT PRIMARY KEY,
    encrypted_blob BLOB NOT NULL,
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version       INTEGER PRIMARY KEY,
    applied_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_CURRENT_VERSION = 2

_V2_COLUMNS = [
    ("service", "TEXT"),
    ("success", "INTEGER"),
    ("latency_ms", "REAL"),
    ("error_type", "TEXT"),
    ("event", "TEXT"),
    ("metadata_json", "TEXT"),
]


async def _migrate_v2(conn: aiosqlite.Connection) -> None:
    """Add analytics columns to audit_log (v1 -> v2)."""
    existing = set()
    async with conn.execute("PRAGMA table_info(audit_log)") as cur:
        async for row in cur:
            existing.add(row[1])

    for col_name, col_type in _V2_COLUMNS:
        if col_name not in existing:
            await conn.execute(f"ALTER TABLE audit_log ADD COLUMN {col_name} {col_type}")

    await conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_service ON audit_log(service)")
    await conn.commit()
    logger.info("Migrated audit_log to v2 (added analytics columns)")


async def init_db(db_path: Path | None = None) -> None:
    """Initialize the database connection and create tables."""
    global _db
    if _db is not None:
        return

    path = db_path or settings.data_dir / "asibot.db"
    path.parent.mkdir(parents=True, exist_ok=True)

    _db = await aiosqlite.connect(str(path))
    _db.row_factory = aiosqlite.Row

    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA busy_timeout=5000")
    await _db.execute("PRAGMA foreign_keys=ON")

    await _db.executescript(_SCHEMA)

    async with _db.execute("SELECT MAX(version) FROM schema_migrations") as cur:
        row = await cur.fetchone()
        current = row[0] if row and row[0] else 0

    if current < 2:
        await _migrate_v2(_db)

    if current < _CURRENT_VERSION:
        await _db.execute(
            "INSERT OR REPLACE INTO schema_migrations (version) VALUES (?)",
            (_CURRENT_VERSION,),
        )
        await _db.commit()

    logger.info("Database initialized at %s (version %d)", path, _CURRENT_VERSION)


async def close_db() -> None:
    """Close the database connection."""
    global _db
    if _db is not None:
        await _db.close()
        _db = None
        logger.info("Database connection closed")


def _get_db() -> aiosqlite.Connection:
    """Get the active database connection. Raises if not initialized."""
    if _db is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _db


# --- Users ---


async def create_user(user_id: str, name: str, api_key: str, created_at: str) -> bool:
    """Create a user. Returns True if created, False if already exists."""
    db = _get_db()
    try:
        await db.execute(
            "INSERT INTO users (user_id, name, api_key, created_at) VALUES (?, ?, ?, ?)",
            (user_id, name, api_key, created_at),
        )
        await db.commit()
        return True
    except aiosqlite.IntegrityError:
        return False


async def get_user_by_key(api_key: str) -> dict | None:
    """Look up user by API key."""
    db = _get_db()
    async with db.execute("SELECT * FROM users WHERE api_key = ?", (api_key,)) as cur:
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_user_by_email(email: str) -> dict | None:
    """Look up user by email (user_id)."""
    db = _get_db()
    async with db.execute("SELECT * FROM users WHERE user_id = ?", (email,)) as cur:
        row = await cur.fetchone()
        return dict(row) if row else None


async def rotate_key(email: str, new_key: str, rotated_at: str) -> dict | None:
    """Rotate API key for a user. Returns updated user or None."""
    db = _get_db()
    await db.execute(
        "UPDATE users SET api_key = ?, key_rotated_at = ? WHERE user_id = ?",
        (new_key, rotated_at, email),
    )
    await db.commit()
    return await get_user_by_email(email)


async def list_users() -> list[dict]:
    """List all users."""
    db = _get_db()
    async with db.execute("SELECT * FROM users") as cur:
        return [dict(row) async for row in cur]


# --- Credentials ---


async def set_credentials(user_id: str, service: str, encrypted_blob: bytes) -> None:
    """Store encrypted credentials for a service."""
    db = _get_db()
    await db.execute(
        "INSERT OR REPLACE INTO credentials (user_id, service, encrypted_blob, updated_at) "
        "VALUES (?, ?, ?, datetime('now'))",
        (user_id, service, encrypted_blob),
    )
    await db.commit()


async def get_credentials(user_id: str, service: str) -> bytes | None:
    """Get encrypted credentials blob for a service."""
    db = _get_db()
    async with db.execute(
        "SELECT encrypted_blob FROM credentials WHERE user_id = ? AND service = ?",
        (user_id, service),
    ) as cur:
        row = await cur.fetchone()
        return row[0] if row else None


async def remove_credentials(user_id: str, service: str) -> None:
    """Remove credentials for a service."""
    db = _get_db()
    await db.execute(
        "DELETE FROM credentials WHERE user_id = ? AND service = ?",
        (user_id, service),
    )
    await db.commit()


async def list_connected(user_id: str) -> list[str]:
    """List service names the user has credentials for."""
    db = _get_db()
    async with db.execute(
        "SELECT service FROM credentials WHERE user_id = ?", (user_id,)
    ) as cur:
        return [row[0] async for row in cur]


# --- Preferences ---


async def get_service_prefs(user_id: str, service: str) -> dict:
    """Get preferences for a service. Returns {} if not set."""
    db = _get_db()
    async with db.execute(
        "SELECT enabled, mode FROM preferences WHERE user_id = ? AND service = ?",
        (user_id, service),
    ) as cur:
        row = await cur.fetchone()
        if row:
            return {"enabled": bool(row[0]), "mode": row[1]}
        return {}


async def set_service_prefs(user_id: str, service: str, enabled: bool, mode: str) -> None:
    """Set preferences for a service."""
    db = _get_db()
    await db.execute(
        "INSERT OR REPLACE INTO preferences (user_id, service, enabled, mode, updated_at) "
        "VALUES (?, ?, ?, ?, datetime('now'))",
        (user_id, service, int(enabled), mode),
    )
    await db.commit()


# --- Audit ---


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
    """Record a tool invocation in the audit log."""
    db = _get_db()
    await db.execute(
        "INSERT INTO audit_log (ts, user_id, tool, args_json, service, success, latency_ms, error_type)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (time.time(), user_id, tool, args_json, service,
         int(success) if success is not None else None,
         round(latency_ms, 2) if latency_ms is not None else None,
         error_type),
    )
    await db.commit()


async def log_event(
    user_id: str | None,
    event: str,
    *,
    service: str | None = None,
    metadata: dict | None = None,
) -> None:
    """Record a lifecycle event (session_start, user_created, etc.)."""
    db = _get_db()
    await db.execute(
        "INSERT INTO audit_log (ts, user_id, event, service, metadata_json)"
        " VALUES (?, ?, ?, ?, ?)",
        (time.time(), user_id, event, service,
         json.dumps(metadata) if metadata else None),
    )
    await db.commit()


async def query_audit(
    *,
    user_id: str | None = None,
    tool: str | None = None,
    since: float | None = None,
    limit: int = 100,
) -> list[dict]:
    """Query audit log entries with optional filters."""
    db = _get_db()
    conditions = []
    params: list = []
    if user_id:
        conditions.append("user_id = ?")
        params.append(user_id)
    if tool:
        conditions.append("tool = ?")
        params.append(tool)
    if since:
        conditions.append("ts > ?")
        params.append(since)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)
    async with db.execute(
        f"SELECT * FROM audit_log {where} ORDER BY ts DESC LIMIT ?", params
    ) as cur:
        return [dict(row) async for row in cur]


async def query_audit_range(start: float, end: float) -> list[dict]:
    """Return all audit entries within [start, end] timestamp range."""
    db = _get_db()
    rows: list[dict] = []
    async with db.execute(
        "SELECT ts, user_id, tool, args_json, service, success, latency_ms,"
        " error_type, event, metadata_json"
        " FROM audit_log WHERE ts >= ? AND ts <= ? ORDER BY ts",
        (start, end),
    ) as cur:
        async for row in cur:
            entry: dict = {"ts": row[0], "user": row[1] or "anonymous"}
            if row[2]:
                entry["tool"] = row[2]
                if row[3]:
                    entry["args"] = row[3]
                if row[4] is not None:
                    entry["service"] = row[4]
                if row[5] is not None:
                    entry["success"] = bool(row[5])
                if row[6] is not None:
                    entry["latency_ms"] = row[6]
                if row[7] is not None:
                    entry["error_type"] = row[7]
            if row[8]:
                entry["event"] = row[8]
                if row[4] is not None:
                    entry["service"] = row[4]
                if row[9]:
                    entry["metadata"] = json.loads(row[9])
            rows.append(entry)
    return rows


async def prune_audit(max_age_days: int = 90) -> int:
    """Delete old audit log entries. Returns count deleted."""
    db = _get_db()
    cutoff = time.time() - (max_age_days * 86400)
    async with db.execute(
        "SELECT COUNT(*) FROM audit_log WHERE ts < ?", (cutoff,)
    ) as cur:
        row = await cur.fetchone()
        count = row[0] if row else 0
    await db.execute("DELETE FROM audit_log WHERE ts < ?", (cutoff,))
    await db.commit()
    return count


# --- Microsoft Tokens ---


async def save_ms_token(user_id: str, encrypted_blob: bytes) -> None:
    """Store encrypted Microsoft token."""
    db = _get_db()
    await db.execute(
        "INSERT OR REPLACE INTO microsoft_tokens (user_id, encrypted_blob, updated_at) "
        "VALUES (?, ?, datetime('now'))",
        (user_id, encrypted_blob),
    )
    await db.commit()


async def load_ms_token(user_id: str) -> bytes | None:
    """Load encrypted Microsoft token blob."""
    db = _get_db()
    async with db.execute(
        "SELECT encrypted_blob FROM microsoft_tokens WHERE user_id = ?", (user_id,)
    ) as cur:
        row = await cur.fetchone()
        return row[0] if row else None


# --- Stats ---


async def db_stats() -> dict:
    """Get database statistics for health check."""
    db = _get_db()
    stats = {}
    for table in ("users", "credentials", "sessions", "audit_log", "microsoft_tokens"):
        async with db.execute(f"SELECT COUNT(*) FROM {table}") as cur:
            row = await cur.fetchone()
            stats[f"{table}_count"] = row[0] if row else 0
    return stats
