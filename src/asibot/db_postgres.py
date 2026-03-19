"""PostgreSQL database backend using asyncpg.

Provides a ``PostgresBackend`` class that implements all the same operations as
the SQLite-based ``db.py`` but targeting PostgreSQL via asyncpg connection pools.
"""

from __future__ import annotations

import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema (idempotent — uses IF NOT EXISTS throughout)
# ---------------------------------------------------------------------------

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS users (
    user_id        TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    api_key        TEXT UNIQUE NOT NULL,
    created_at     TEXT NOT NULL,
    key_rotated_at TEXT,
    role           TEXT NOT NULL DEFAULT 'user'
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_api_key ON users(api_key);

CREATE TABLE IF NOT EXISTS credentials (
    user_id        TEXT NOT NULL,
    service        TEXT NOT NULL,
    encrypted_blob BYTEA NOT NULL,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, service)
);

CREATE TABLE IF NOT EXISTS preferences (
    user_id    TEXT NOT NULL,
    service    TEXT NOT NULL,
    enabled    BOOLEAN NOT NULL DEFAULT TRUE,
    mode       TEXT NOT NULL DEFAULT 'read',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, service)
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    expires_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

CREATE TABLE IF NOT EXISTS audit_log (
    id            BIGSERIAL PRIMARY KEY,
    ts            DOUBLE PRECISION NOT NULL,
    user_id       TEXT,
    tool          TEXT,
    args_json     TEXT,
    service       TEXT,
    success       BOOLEAN,
    latency_ms    DOUBLE PRECISION,
    error_type    TEXT,
    event         TEXT,
    metadata_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_service ON audit_log(service);

CREATE TABLE IF NOT EXISTS microsoft_tokens (
    user_id        TEXT PRIMARY KEY,
    encrypted_blob BYTEA NOT NULL,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pending_setups (
    setup_id   TEXT PRIMARY KEY,
    user_id    TEXT,
    service    TEXT,
    state      JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pending_setups_expires ON pending_setups(expires_at);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

_CURRENT_VERSION = 5

# Columns added in the v1 -> v2 migration.
_V2_COLUMNS = [
    ("service", "TEXT"),
    ("success", "BOOLEAN"),
    ("latency_ms", "DOUBLE PRECISION"),
    ("error_type", "TEXT"),
    ("event", "TEXT"),
    ("metadata_json", "TEXT"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _record_to_dict(record: asyncpg.Record | None) -> dict | None:
    """Convert an asyncpg.Record to a plain dict, or return None."""
    if record is None:
        return None
    return dict(record)


def _records_to_dicts(records: list[asyncpg.Record]) -> list[dict]:
    """Convert a list of asyncpg.Record objects to a list of dicts."""
    return [dict(r) for r in records]


# ---------------------------------------------------------------------------
# PostgresBackend
# ---------------------------------------------------------------------------


class PostgresBackend:
    """Async PostgreSQL backend backed by an asyncpg connection pool.

    Parameters
    ----------
    database_url : str
        PostgreSQL DSN, e.g. ``postgresql://user:pass@host:5432/dbname``.
    min_size : int
        Minimum number of connections maintained in the pool.
    max_size : int
        Maximum number of connections in the pool.
    """

    def __init__(
        self,
        database_url: str,
        min_size: int = 5,
        max_size: int = 20,
        read_url: str = "",
    ) -> None:
        self._database_url = database_url
        self._min_size = min_size
        self._max_size = max_size
        self._read_url = read_url or database_url
        self._pool: asyncpg.Pool | None = None
        self._read_pool: asyncpg.Pool | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Create the connection pool, apply schema, and run migrations."""
        self._pool = await asyncpg.create_pool(
            self._database_url,
            min_size=self._min_size,
            max_size=self._max_size,
        )
        logger.info(
            "PostgreSQL pool created (min=%d, max=%d)",
            self._min_size,
            self._max_size,
        )

        # Initialize read replica pool if configured
        if self._read_url != self._database_url:
            try:
                self._read_pool = await asyncpg.create_pool(
                    self._read_url,
                    min_size=self._min_size,
                    max_size=self._max_size,
                )
                logger.info("PostgreSQL read replica pool initialized")
            except Exception:
                logger.warning(
                    "Failed to connect to read replica, falling back to primary",
                    exc_info=True,
                )
                self._read_pool = self._pool
        else:
            self._read_pool = self._pool

        # Apply base schema (all statements are idempotent).
        async with self._pool.acquire() as conn:
            await conn.execute(_SCHEMA)

        # Run migrations.
        await self._run_migrations()
        logger.info("PostgresBackend initialised (schema version %d)", _CURRENT_VERSION)

    async def close(self) -> None:
        """Gracefully close the connection pool(s)."""
        if self._read_pool is not None and self._read_pool is not self._pool:
            await self._read_pool.close()
            self._read_pool = None
            logger.info("PostgreSQL read replica pool closed")
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            self._read_pool = None
            logger.info("PostgreSQL pool closed")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError(
                "PostgresBackend not initialised. Call initialize() first."
            )
        return self._pool

    def _get_read_pool(self) -> asyncpg.Pool:
        """Return read-only pool (replica if configured, else primary)."""
        return self._read_pool or self._get_pool()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[asyncpg.Connection]:
        """Acquire a connection and begin a transaction.

        Usage::

            async with backend.transaction() as conn:
                await conn.execute("UPDATE ...")
                await conn.execute("INSERT ...")

        If the block raises, the transaction is rolled back automatically.
        On normal exit the transaction is committed.
        """
        pool = self._get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                yield conn

    # ------------------------------------------------------------------
    # Migrations
    # ------------------------------------------------------------------

    async def _run_migrations(self) -> None:
        pool = self._get_pool()

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT MAX(version) AS v FROM schema_migrations"
            )
            current = row["v"] if row and row["v"] is not None else 0

        if current < 2:
            await self._migrate_v2()

        if current < 3:
            await self._migrate_v3()

        if current < 4:
            await self._migrate_v4()

        if current < 5:
            await self._migrate_v5()

        if current < _CURRENT_VERSION:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO schema_migrations (version)
                    VALUES ($1)
                    ON CONFLICT (version) DO UPDATE SET applied_at = NOW()
                    """,
                    _CURRENT_VERSION,
                )
            logger.info("Schema migrations applied up to version %d", _CURRENT_VERSION)

    async def _migrate_v2(self) -> None:
        """Add analytics columns to audit_log (v1 -> v2)."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            # Discover existing columns.
            existing = set()
            rows = await conn.fetch(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'audit_log'
                """
            )
            for r in rows:
                existing.add(r["column_name"])

            for col_name, col_type in _V2_COLUMNS:
                if col_name not in existing:
                    await conn.execute(
                        f"ALTER TABLE audit_log ADD COLUMN {col_name} {col_type}"
                    )

            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_service ON audit_log(service)"
            )
        logger.info("Migrated audit_log to v2 (added analytics columns)")

    async def _migrate_v3(self) -> None:
        """Add role column to users table (v2 -> v3)."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            existing = set()
            rows = await conn.fetch(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'users'
                """
            )
            for r in rows:
                existing.add(r["column_name"])

            if "role" not in existing:
                await conn.execute(
                    "ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'"
                )
                # Promote the earliest user to admin.
                await conn.execute(
                    """
                    UPDATE users SET role = 'admin'
                    WHERE user_id = (
                        SELECT user_id FROM users ORDER BY created_at ASC LIMIT 1
                    )
                    """
                )
        logger.info("Migrated users to v3 (added role column, first user set to admin)")

    async def _migrate_v4(self) -> None:
        """Add foreign key constraints and missing indexes (v3 -> v4)."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            # FK: credentials.user_id -> users.user_id ON DELETE CASCADE
            await conn.execute("""
                DO $$ BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.table_constraints
                        WHERE constraint_name = 'fk_credentials_user'
                          AND table_name = 'credentials'
                    ) THEN
                        ALTER TABLE credentials
                            ADD CONSTRAINT fk_credentials_user
                            FOREIGN KEY (user_id) REFERENCES users(user_id)
                            ON DELETE CASCADE;
                    END IF;
                END $$;
            """)

            # FK: preferences.user_id -> users.user_id ON DELETE CASCADE
            await conn.execute("""
                DO $$ BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.table_constraints
                        WHERE constraint_name = 'fk_preferences_user'
                          AND table_name = 'preferences'
                    ) THEN
                        ALTER TABLE preferences
                            ADD CONSTRAINT fk_preferences_user
                            FOREIGN KEY (user_id) REFERENCES users(user_id)
                            ON DELETE CASCADE;
                    END IF;
                END $$;
            """)

            # FK: sessions.user_id -> users.user_id ON DELETE CASCADE
            await conn.execute("""
                DO $$ BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.table_constraints
                        WHERE constraint_name = 'fk_sessions_user'
                          AND table_name = 'sessions'
                    ) THEN
                        ALTER TABLE sessions
                            ADD CONSTRAINT fk_sessions_user
                            FOREIGN KEY (user_id) REFERENCES users(user_id)
                            ON DELETE CASCADE;
                    END IF;
                END $$;
            """)

            # Index on credentials(user_id) for efficient list_connected() queries
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_credentials_user ON credentials(user_id)"
            )

            # FK: microsoft_tokens.user_id -> users.user_id ON DELETE CASCADE
            await conn.execute("""
                DO $$ BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.table_constraints
                        WHERE constraint_name = 'fk_microsoft_tokens_user'
                          AND table_name = 'microsoft_tokens'
                    ) THEN
                        ALTER TABLE microsoft_tokens
                            ADD CONSTRAINT fk_microsoft_tokens_user
                            FOREIGN KEY (user_id) REFERENCES users(user_id)
                            ON DELETE CASCADE;
                    END IF;
                END $$;
            """)
        logger.info("Migrated to v4 (added FK constraints and credentials index)")

    async def _migrate_v5(self) -> None:
        """Add pending_setups table (v4 -> v5)."""
        pool = self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS pending_setups (
                    setup_id   TEXT PRIMARY KEY,
                    user_id    TEXT,
                    service    TEXT,
                    state      JSONB NOT NULL DEFAULT '{}',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    expires_at TIMESTAMPTZ NOT NULL
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_pending_setups_expires "
                "ON pending_setups(expires_at)"
            )
        logger.info("Migrated to v5 (added pending_setups table)")

    # ==================================================================
    # Users
    # ==================================================================

    async def create_user(
        self,
        user_id: str,
        name: str,
        api_key: str,
        created_at: str,
        role: str = "user",
    ) -> bool:
        """Create a user. Returns True if created, False if already exists."""
        pool = self._get_pool()
        try:
            await pool.execute(
                """
                INSERT INTO users (user_id, name, api_key, created_at, role)
                VALUES ($1, $2, $3, $4, $5)
                """,
                user_id,
                name,
                api_key,
                created_at,
                role,
            )
            return True
        except asyncpg.UniqueViolationError:
            return False

    async def get_user_by_key(self, api_key: str) -> dict | None:
        """Look up a user by API key."""
        pool = self._get_read_pool()
        row = await pool.fetchrow(
            "SELECT * FROM users WHERE api_key = $1", api_key
        )
        return _record_to_dict(row)

    async def get_user_by_email(self, email: str) -> dict | None:
        """Look up a user by email (user_id)."""
        pool = self._get_read_pool()
        row = await pool.fetchrow(
            "SELECT * FROM users WHERE user_id = $1", email
        )
        return _record_to_dict(row)

    async def rotate_key(
        self, email: str, new_key: str, rotated_at: str
    ) -> dict | None:
        """Rotate the API key for a user. Returns updated user dict or None."""
        pool = self._get_pool()
        await pool.execute(
            "UPDATE users SET api_key = $1, key_rotated_at = $2 WHERE user_id = $3",
            new_key,
            rotated_at,
            email,
        )
        return await self.get_user_by_email(email)

    async def list_users(self) -> list[dict]:
        """Return all users."""
        pool = self._get_read_pool()
        rows = await pool.fetch("SELECT * FROM users")
        return _records_to_dicts(rows)

    async def set_role(self, email: str, role: str) -> dict | None:
        """Update a user's role. Returns updated user dict or None."""
        pool = self._get_pool()
        result = await pool.execute(
            "UPDATE users SET role = $1 WHERE user_id = $2", role, email
        )
        # asyncpg returns "UPDATE 0" if no rows matched
        if result.split()[-1] == "0":
            return None
        return await self.get_user_by_email(email)

    async def set_role_with_audit(
        self,
        email: str,
        role: str,
        *,
        admin_id: str | None = None,
    ) -> dict | None:
        """Update a user's role and log an audit event atomically.

        Both the role UPDATE and the audit INSERT happen inside a single
        database transaction.  If either fails the entire change is rolled back.
        """
        async with self.transaction() as conn:
            result = await conn.execute(
                "UPDATE users SET role = $1 WHERE user_id = $2", role, email
            )
            if result.split()[-1] == "0":
                return None
            await conn.execute(
                """
                INSERT INTO audit_log (ts, user_id, event, service, metadata_json)
                VALUES ($1, $2, $3, $4, $5)
                """,
                time.time(),
                admin_id,
                "role_changed",
                None,
                json.dumps({"target": email, "new_role": role}),
            )
            row = await conn.fetchrow(
                "SELECT * FROM users WHERE user_id = $1", email
            )
            return _record_to_dict(row)

    async def set_credentials_with_prefs(
        self,
        user_id: str,
        service: str,
        encrypted_blob: bytes,
        enabled: bool = True,
        mode: str = "read",
    ) -> None:
        """Store credentials and set default preferences in a single transaction.

        Ensures both the credential blob and the service preferences are
        persisted atomically — a crash between the two writes cannot leave
        the user in an inconsistent state.
        """
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        async with self.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO credentials (user_id, service, encrypted_blob, updated_at)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (user_id, service) DO UPDATE SET
                    encrypted_blob = EXCLUDED.encrypted_blob,
                    updated_at     = EXCLUDED.updated_at
                """,
                user_id,
                service,
                encrypted_blob,
                now,
            )
            # Only insert preferences if the pair doesn't already exist
            await conn.execute(
                """
                INSERT INTO preferences (user_id, service, enabled, mode, updated_at)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (user_id, service) DO NOTHING
                """,
                user_id,
                service,
                enabled,
                mode,
                now,
            )

    # ==================================================================
    # Credentials
    # ==================================================================

    async def set_credentials(
        self, user_id: str, service: str, encrypted_blob: bytes
    ) -> None:
        """Store encrypted credentials for a service."""
        pool = self._get_pool()
        await pool.execute(
            """
            INSERT INTO credentials (user_id, service, encrypted_blob, updated_at)
            VALUES ($1, $2, $3, NOW())
            ON CONFLICT (user_id, service)
            DO UPDATE SET encrypted_blob = EXCLUDED.encrypted_blob,
                          updated_at     = NOW()
            """,
            user_id,
            service,
            encrypted_blob,
        )

    async def get_credentials(self, user_id: str, service: str) -> bytes | None:
        """Get the encrypted credentials blob for a service."""
        pool = self._get_read_pool()
        row = await pool.fetchrow(
            "SELECT encrypted_blob FROM credentials WHERE user_id = $1 AND service = $2",
            user_id,
            service,
        )
        return row["encrypted_blob"] if row else None

    async def remove_credentials(self, user_id: str, service: str) -> None:
        """Remove credentials for a service."""
        pool = self._get_pool()
        await pool.execute(
            "DELETE FROM credentials WHERE user_id = $1 AND service = $2",
            user_id,
            service,
        )

    async def list_connected(self, user_id: str) -> list[str]:
        """List service names the user has credentials for."""
        pool = self._get_read_pool()
        rows = await pool.fetch(
            "SELECT service FROM credentials WHERE user_id = $1", user_id
        )
        return [r["service"] for r in rows]

    # ==================================================================
    # Preferences
    # ==================================================================

    async def get_service_prefs(self, user_id: str, service: str) -> dict:
        """Get preferences for a service. Returns {} if not set."""
        pool = self._get_read_pool()
        row = await pool.fetchrow(
            "SELECT enabled, mode FROM preferences WHERE user_id = $1 AND service = $2",
            user_id,
            service,
        )
        if row:
            return {"enabled": bool(row["enabled"]), "mode": row["mode"]}
        return {}

    async def set_service_prefs(
        self, user_id: str, service: str, enabled: bool, mode: str
    ) -> None:
        """Set preferences for a service."""
        pool = self._get_pool()
        await pool.execute(
            """
            INSERT INTO preferences (user_id, service, enabled, mode, updated_at)
            VALUES ($1, $2, $3, $4, NOW())
            ON CONFLICT (user_id, service)
            DO UPDATE SET enabled    = EXCLUDED.enabled,
                          mode       = EXCLUDED.mode,
                          updated_at = NOW()
            """,
            user_id,
            service,
            enabled,
            mode,
        )

    # ==================================================================
    # Sessions
    # ==================================================================

    async def cache_session(
        self, session_id: str, user_id: str, ttl: int
    ) -> None:
        """Persist a session to the database."""
        now = time.time()
        pool = self._get_pool()
        await pool.execute(
            """
            INSERT INTO sessions (session_id, user_id, created_at, expires_at)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (session_id)
            DO UPDATE SET user_id    = EXCLUDED.user_id,
                          created_at = EXCLUDED.created_at,
                          expires_at = EXCLUDED.expires_at
            """,
            session_id,
            user_id,
            now,
            now + ttl,
        )

    async def get_session_user(self, session_id: str) -> str | None:
        """Look up a session, returning the user_id if still valid."""
        pool = self._get_read_pool()
        row = await pool.fetchrow(
            "SELECT user_id FROM sessions WHERE session_id = $1 AND expires_at > $2",
            session_id,
            time.time(),
        )
        return row["user_id"] if row else None

    async def load_active_sessions(self) -> dict[str, tuple[str, float]]:
        """Load all non-expired sessions.

        Returns ``{session_id: (user_id, created_at)}``.
        """
        pool = self._get_read_pool()
        rows = await pool.fetch(
            "SELECT session_id, user_id, created_at FROM sessions WHERE expires_at > $1",
            time.time(),
        )
        return {r["session_id"]: (r["user_id"], r["created_at"]) for r in rows}

    async def delete_user_sessions(self, user_id: str) -> int:
        """Remove all sessions for a user. Returns number of rows deleted."""
        pool = self._get_pool()
        result = await pool.execute(
            "DELETE FROM sessions WHERE user_id = $1", user_id
        )
        # asyncpg returns a status string like "DELETE 3"
        return int(result.split()[-1])

    async def purge_expired_sessions(self) -> int:
        """Delete expired sessions. Returns number of rows deleted."""
        pool = self._get_pool()
        result = await pool.execute(
            "DELETE FROM sessions WHERE expires_at <= $1", time.time()
        )
        return int(result.split()[-1])

    # ==================================================================
    # Audit
    # ==================================================================

    async def log_audit(
        self,
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
        pool = self._get_pool()
        await pool.execute(
            """
            INSERT INTO audit_log
                (ts, user_id, tool, args_json, service, success, latency_ms, error_type)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            time.time(),
            user_id,
            tool,
            args_json,
            service,
            success,
            round(latency_ms, 2) if latency_ms is not None else None,
            error_type,
        )

    async def log_event(
        self,
        user_id: str | None,
        event: str,
        *,
        service: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        """Record a lifecycle event (session_start, user_created, etc.)."""
        pool = self._get_pool()
        await pool.execute(
            """
            INSERT INTO audit_log (ts, user_id, event, service, metadata_json)
            VALUES ($1, $2, $3, $4, $5)
            """,
            time.time(),
            user_id,
            event,
            service,
            json.dumps(metadata) if metadata else None,
        )

    async def query_audit(
        self,
        *,
        user_id: str | None = None,
        tool: str | None = None,
        since: float | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Query audit log entries with optional filters."""
        pool = self._get_read_pool()
        conditions: list[str] = []
        params: list[Any] = []
        idx = 1

        if user_id:
            conditions.append(f"user_id = ${idx}")
            params.append(user_id)
            idx += 1
        if tool:
            conditions.append(f"tool = ${idx}")
            params.append(tool)
            idx += 1
        if since:
            conditions.append(f"ts > ${idx}")
            params.append(since)
            idx += 1

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        query = (
            f"SELECT * FROM audit_log {where} ORDER BY ts DESC LIMIT ${idx}"
        )
        params.append(limit)

        rows = await pool.fetch(query, *params)
        return _records_to_dicts(rows)

    async def query_audit_range(
        self, start: float, end: float, user_id: str | None = None,
    ) -> list[dict]:
        """Return all audit entries within [start, end] timestamp range.

        If user_id is provided, only return entries for that user (dashboard scoping).
        """
        pool = self._get_read_pool()
        if user_id is not None:
            rows = await pool.fetch(
                """
                SELECT ts, user_id, tool, args_json, service, success, latency_ms,
                       error_type, event, metadata_json
                FROM audit_log
                WHERE ts >= $1 AND ts <= $2 AND user_id = $3
                ORDER BY ts
                """,
                start,
                end,
                user_id,
            )
        else:
            rows = await pool.fetch(
                """
                SELECT ts, user_id, tool, args_json, service, success, latency_ms,
                       error_type, event, metadata_json
                FROM audit_log
                WHERE ts >= $1 AND ts <= $2
                ORDER BY ts
                """,
                start,
                end,
            )
        results: list[dict] = []
        for row in rows:
            entry: dict[str, Any] = {
                "ts": row["ts"],
                "user": row["user_id"] or "anonymous",
            }
            if row["tool"]:
                entry["tool"] = row["tool"]
                if row["args_json"]:
                    entry["args"] = row["args_json"]
                if row["service"] is not None:
                    entry["service"] = row["service"]
                if row["success"] is not None:
                    entry["success"] = bool(row["success"])
                if row["latency_ms"] is not None:
                    entry["latency_ms"] = row["latency_ms"]
                if row["error_type"] is not None:
                    entry["error_type"] = row["error_type"]
            if row["event"]:
                entry["event"] = row["event"]
                if row["service"] is not None:
                    entry["service"] = row["service"]
                if row["metadata_json"]:
                    entry["metadata"] = json.loads(row["metadata_json"])
            results.append(entry)
        return results

    async def prune_audit(self, max_age_days: int = 90) -> int:
        """Delete old audit log entries. Returns count deleted."""
        pool = self._get_pool()
        cutoff = time.time() - (max_age_days * 86400)
        row = await pool.fetchrow(
            "SELECT COUNT(*) AS cnt FROM audit_log WHERE ts < $1", cutoff
        )
        count = row["cnt"] if row else 0
        await pool.execute("DELETE FROM audit_log WHERE ts < $1", cutoff)
        return count

    # ==================================================================
    # Microsoft tokens
    # ==================================================================

    async def save_ms_token(self, user_id: str, encrypted_blob: bytes) -> None:
        """Store encrypted Microsoft token."""
        pool = self._get_pool()
        await pool.execute(
            """
            INSERT INTO microsoft_tokens (user_id, encrypted_blob, updated_at)
            VALUES ($1, $2, NOW())
            ON CONFLICT (user_id)
            DO UPDATE SET encrypted_blob = EXCLUDED.encrypted_blob,
                          updated_at     = NOW()
            """,
            user_id,
            encrypted_blob,
        )

    async def load_ms_token(self, user_id: str) -> bytes | None:
        """Load encrypted Microsoft token blob."""
        pool = self._get_pool()
        row = await pool.fetchrow(
            "SELECT encrypted_blob FROM microsoft_tokens WHERE user_id = $1",
            user_id,
        )
        return row["encrypted_blob"] if row else None

    # ==================================================================
    # Pending Setups (OAuth state persistence)
    # ==================================================================

    async def store_pending_setup(
        self,
        setup_id: str,
        state: dict,
        *,
        user_id: str | None = None,
        service: str | None = None,
        ttl: int = 900,
    ) -> None:
        """Persist an OAuth pending-setup entry.

        Parameters
        ----------
        setup_id : str
            Unique token for the setup flow.
        state : dict
            Arbitrary JSON-serialisable state (status, user, error, etc.).
        user_id : str | None
            Optional user email (may be unknown at start of flow).
        service : str | None
            Optional service name (e.g. "github", "google").
        ttl : int
            Time-to-live in seconds (default 900 = 15 minutes).
        """
        pool = self._get_pool()
        await pool.execute(
            """
            INSERT INTO pending_setups (setup_id, user_id, service, state, created_at, expires_at)
            VALUES ($1, $2, $3, $4::jsonb, NOW(), NOW() + make_interval(secs => $5))
            ON CONFLICT (setup_id)
            DO UPDATE SET state      = $4::jsonb,
                          user_id    = COALESCE(EXCLUDED.user_id, pending_setups.user_id),
                          service    = COALESCE(EXCLUDED.service, pending_setups.service),
                          expires_at = NOW() + make_interval(secs => $5)
            """,
            setup_id,
            user_id,
            service,
            json.dumps(state),
            float(ttl),
        )

    async def get_pending_setup(self, setup_id: str) -> dict | None:
        """Retrieve a pending setup by ID. Returns None if not found or expired."""
        pool = self._get_pool()
        row = await pool.fetchrow(
            """
            SELECT setup_id, user_id, service, state, created_at, expires_at
            FROM pending_setups
            WHERE setup_id = $1 AND expires_at > NOW()
            """,
            setup_id,
        )
        if row is None:
            return None
        state = row["state"] if isinstance(row["state"], dict) else json.loads(row["state"])
        return {
            "setup_id": row["setup_id"],
            "user_id": row["user_id"],
            "service": row["service"],
            "state": state,
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
        }

    async def delete_pending_setup(self, setup_id: str) -> None:
        """Remove a pending setup entry."""
        pool = self._get_pool()
        await pool.execute(
            "DELETE FROM pending_setups WHERE setup_id = $1", setup_id
        )

    async def prune_expired_setups(self) -> int:
        """Delete all expired pending setup entries. Returns count deleted."""
        pool = self._get_pool()
        result = await pool.execute(
            "DELETE FROM pending_setups WHERE expires_at <= NOW()"
        )
        return int(result.split()[-1])

    # ==================================================================
    # Stats
    # ==================================================================

    async def db_stats(self) -> dict:
        """Get database statistics for health checks."""
        pool = self._get_read_pool()
        stats: dict[str, Any] = {}
        for table in (
            "users",
            "credentials",
            "sessions",
            "audit_log",
            "microsoft_tokens",
        ):
            row = await pool.fetchrow(f"SELECT COUNT(*) AS cnt FROM {table}")  # noqa: S608
            stats[f"{table}_count"] = row["cnt"] if row else 0

        # Include pool-level diagnostics (primary pool).
        primary = self._get_pool()
        stats["pool_size"] = primary.get_size()
        stats["pool_free"] = primary.get_idle_size()
        stats["pool_min"] = primary.get_min_size()
        stats["pool_max"] = primary.get_max_size()

        # Include read pool diagnostics if separate from primary.
        if pool is not primary:
            stats["read_pool_size"] = pool.get_size()
            stats["read_pool_free"] = pool.get_idle_size()
            stats["read_pool_min"] = pool.get_min_size()
            stats["read_pool_max"] = pool.get_max_size()

        return stats
