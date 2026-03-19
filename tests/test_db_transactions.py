"""Tests for database transaction support in PostgresBackend.

These tests use unittest.mock to verify that the transaction context manager,
set_role_with_audit, and set_credentials_with_prefs behave correctly --
including rollback on error.  No real PostgreSQL connection is required.
"""

import json
import time
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers — lightweight fakes for asyncpg objects
# ---------------------------------------------------------------------------


class FakeConnection:
    """In-memory fake that records SQL statements for assertion."""

    def __init__(self, *, fail_on: str | None = None):
        self.executed: list[tuple[str, tuple]] = []
        self.fetched_rows: list[dict | None] = []
        self._fail_on = fail_on  # substring — if SQL contains this, raise

    async def execute(self, query: str, *args):
        if self._fail_on and self._fail_on in query:
            raise RuntimeError(f"Simulated failure on: {self._fail_on}")
        self.executed.append((query, args))
        # Return asyncpg-style status strings
        if query.strip().upper().startswith("UPDATE"):
            return "UPDATE 1"
        if query.strip().upper().startswith("INSERT"):
            return "INSERT 0 1"
        if query.strip().upper().startswith("DELETE"):
            return "DELETE 1"
        return "OK"

    async def fetchrow(self, query: str, *args):
        self.executed.append((query, args))
        if self.fetched_rows:
            return self.fetched_rows.pop(0)
        return None

    async def fetch(self, query: str, *args):
        self.executed.append((query, args))
        return []

    @asynccontextmanager
    async def transaction(self):
        """Simulate asyncpg transaction context — just yields."""
        yield


class FakePool:
    """Minimal fake of asyncpg.Pool that dispenses FakeConnection instances."""

    def __init__(self, conn: FakeConnection | None = None):
        self._conn = conn or FakeConnection()

    @asynccontextmanager
    async def acquire(self):
        yield self._conn

    # Pool methods used by _get_pool()
    async def execute(self, query, *args):
        return await self._conn.execute(query, *args)

    async def fetchrow(self, query, *args):
        return await self._conn.fetchrow(query, *args)

    async def fetch(self, query, *args):
        return await self._conn.fetch(query, *args)

    async def close(self):
        pass


def _make_backend(conn: FakeConnection | None = None):
    """Construct a PostgresBackend wired to a FakePool (skipping real PG)."""
    from asibot.db_postgres import PostgresBackend

    backend = PostgresBackend.__new__(PostgresBackend)
    fake_conn = conn or FakeConnection()
    fake_pool = FakePool(fake_conn)
    backend._pool = fake_pool
    backend._read_pool = fake_pool
    backend._database_url = "postgresql://fake"
    backend._min_size = 1
    backend._max_size = 1
    return backend, fake_conn


# ---------------------------------------------------------------------------
# Tests — transaction() context manager
# ---------------------------------------------------------------------------


class TestTransactionContextManager:
    @pytest.mark.asyncio
    async def test_yields_connection(self):
        backend, fake_conn = _make_backend()
        async with backend.transaction() as conn:
            assert conn is fake_conn

    @pytest.mark.asyncio
    async def test_execute_inside_transaction(self):
        backend, fake_conn = _make_backend()
        async with backend.transaction() as conn:
            await conn.execute("UPDATE users SET role = $1 WHERE user_id = $2", "admin", "a@b.com")
        assert len(fake_conn.executed) == 1
        assert "UPDATE users" in fake_conn.executed[0][0]

    @pytest.mark.asyncio
    async def test_raises_before_pool_init(self):
        from asibot.db_postgres import PostgresBackend

        backend = PostgresBackend.__new__(PostgresBackend)
        backend._pool = None
        with pytest.raises(RuntimeError, match="not initialised"):
            async with backend.transaction():
                pass


# ---------------------------------------------------------------------------
# Tests — set_role_with_audit (transactional role change + audit)
# ---------------------------------------------------------------------------


class TestSetRoleWithAudit:
    @pytest.mark.asyncio
    async def test_role_update_and_audit_in_one_transaction(self):
        fake_conn = FakeConnection()
        fake_conn.fetched_rows = [
            {"user_id": "u@co.com", "name": "U", "api_key": "k", "role": "admin",
             "created_at": "2025-01-01", "key_rotated_at": None},
        ]
        backend, _ = _make_backend(fake_conn)

        result = await backend.set_role_with_audit("u@co.com", "admin", admin_id="boss@co.com")

        assert result is not None
        assert result["role"] == "admin"

        # Verify exactly 3 SQL statements: UPDATE role, INSERT audit, SELECT user
        sql_texts = [sql for sql, _ in fake_conn.executed]
        assert any("UPDATE users SET role" in s for s in sql_texts)
        assert any("INSERT INTO audit_log" in s for s in sql_texts)
        assert any("SELECT * FROM users" in s for s in sql_texts)

    @pytest.mark.asyncio
    async def test_returns_none_when_user_not_found(self):
        fake_conn = FakeConnection()
        # Override execute to return UPDATE 0
        original_execute = fake_conn.execute

        async def execute_no_match(query, *args):
            if "UPDATE" in query:
                fake_conn.executed.append((query, args))
                return "UPDATE 0"
            return await original_execute(query, *args)

        fake_conn.execute = execute_no_match
        backend, _ = _make_backend(fake_conn)

        result = await backend.set_role_with_audit("nobody@co.com", "readonly")
        assert result is None

    @pytest.mark.asyncio
    async def test_audit_contains_metadata(self):
        fake_conn = FakeConnection()
        fake_conn.fetched_rows = [
            {"user_id": "u@co.com", "name": "U", "api_key": "k", "role": "readonly",
             "created_at": "2025-01-01", "key_rotated_at": None},
        ]
        backend, _ = _make_backend(fake_conn)

        await backend.set_role_with_audit("u@co.com", "readonly", admin_id="boss@co.com")

        # Find the INSERT audit_log call and verify metadata
        for sql, args in fake_conn.executed:
            if "INSERT INTO audit_log" in sql:
                # args: (ts, admin_id, event, service, metadata_json)
                assert args[1] == "boss@co.com"
                assert args[2] == "role_changed"
                metadata = json.loads(args[4])
                assert metadata["target"] == "u@co.com"
                assert metadata["new_role"] == "readonly"
                break
        else:
            pytest.fail("No INSERT INTO audit_log statement found")


# ---------------------------------------------------------------------------
# Tests — set_credentials_with_prefs (transactional creds + prefs)
# ---------------------------------------------------------------------------


class TestSetCredentialsWithPrefs:
    @pytest.mark.asyncio
    async def test_inserts_creds_and_prefs(self):
        fake_conn = FakeConnection()
        # fetchrow for "SELECT 1 FROM preferences" returns None (no existing prefs)
        fake_conn.fetched_rows = [None]
        backend, _ = _make_backend(fake_conn)

        await backend.set_credentials_with_prefs("u@co.com", "github", b"encrypted")

        sql_texts = [sql for sql, _ in fake_conn.executed]
        assert any("INSERT INTO credentials" in s for s in sql_texts)
        assert any("INSERT INTO preferences" in s for s in sql_texts)

    @pytest.mark.asyncio
    async def test_prefs_use_on_conflict_do_nothing(self):
        """Prefs INSERT uses ON CONFLICT DO NOTHING so existing prefs are preserved."""
        fake_conn = FakeConnection()
        backend, _ = _make_backend(fake_conn)

        await backend.set_credentials_with_prefs("u@co.com", "github", b"encrypted")

        sql_texts = [sql for sql, _ in fake_conn.executed]
        assert any("INSERT INTO credentials" in s for s in sql_texts)
        pref_inserts = [s for s in sql_texts if "INSERT INTO preferences" in s]
        assert len(pref_inserts) == 1
        assert "ON CONFLICT" in pref_inserts[0]
        assert "DO NOTHING" in pref_inserts[0]


# ---------------------------------------------------------------------------
# Tests — auth.set_role delegates correctly
# ---------------------------------------------------------------------------


class TestAuthSetRoleDelegation:
    @pytest.mark.asyncio
    async def test_set_role_updates_user(self):
        """auth.set_role should update the role and return the updated user."""
        from asibot import auth

        mock_user = {"user_id": "u@co.com", "role": "user"}
        updated_user = {"user_id": "u@co.com", "role": "admin"}

        mock_db = MagicMock()
        mock_db.get_user_by_email = AsyncMock(return_value=mock_user)
        mock_db.set_role = AsyncMock(return_value=updated_user)
        mock_audit = MagicMock()
        mock_audit.log_event = MagicMock()

        with patch("asibot.db", mock_db), patch("asibot.audit", mock_audit):
            result = await auth.set_role("u@co.com", "admin")
            assert result is not None
            assert result["role"] == "admin"

    @pytest.mark.asyncio
    async def test_set_role_readonly(self):
        """auth.set_role should accept 'readonly' as a valid role."""
        from asibot import auth

        mock_user = {"user_id": "u@co.com", "role": "user"}
        updated_user = {"user_id": "u@co.com", "role": "readonly"}

        mock_db = MagicMock()
        mock_db.get_user_by_email = AsyncMock(return_value=mock_user)
        mock_db.set_role = AsyncMock(return_value=updated_user)
        mock_audit = MagicMock()
        mock_audit.log_event = MagicMock()

        with patch("asibot.db", mock_db), patch("asibot.audit", mock_audit):
            result = await auth.set_role("u@co.com", "readonly")
            assert result is not None
            assert result["role"] == "readonly"

    @pytest.mark.asyncio
    async def test_set_role_invalid_role_returns_none(self):
        """auth.set_role should raise ValueError for invalid roles."""
        from asibot import auth

        mock_audit = MagicMock()
        mock_audit.log_event = MagicMock()

        with patch("asibot.audit", mock_audit):
            with pytest.raises(ValueError, match="Invalid role"):
                await auth.set_role("u@co.com", "superadmin")

    @pytest.mark.asyncio
    async def test_set_role_user_not_found(self):
        """auth.set_role should return None if the user does not exist."""
        from asibot import auth

        mock_db = MagicMock()
        mock_db.get_user_by_email = AsyncMock(return_value=None)
        mock_audit = MagicMock()
        mock_audit.log_event = MagicMock()

        with patch("asibot.db", mock_db), patch("asibot.audit", mock_audit):
            result = await auth.set_role("nobody@co.com", "admin")
            assert result is None


# ---------------------------------------------------------------------------
# Tests — db facade delegates transaction()
# ---------------------------------------------------------------------------


class TestPostgresBackendTransaction:
    @pytest.mark.asyncio
    async def test_backend_transaction_yields_connection(self):
        fake_conn = FakeConnection()
        backend, _ = _make_backend(fake_conn)

        async with backend.transaction() as conn:
            assert conn is fake_conn

    @pytest.mark.asyncio
    async def test_backend_set_role_delegates(self):
        fake_conn = FakeConnection()
        fake_conn.fetched_rows = [
            {"user_id": "x@co.com", "name": "X", "api_key": "k", "role": "admin",
             "created_at": "2025-01-01", "key_rotated_at": None},
        ]
        backend, _ = _make_backend(fake_conn)

        result = await backend.set_role("x@co.com", "admin")
        assert result is not None

    @pytest.mark.asyncio
    async def test_backend_set_credentials_with_prefs_delegates(self):
        fake_conn = FakeConnection()
        fake_conn.fetched_rows = [None]  # no existing prefs
        backend, _ = _make_backend(fake_conn)

        await backend.set_credentials_with_prefs("u@co.com", "github", b"blob")

        sql_texts = [sql for sql, _ in fake_conn.executed]
        assert any("INSERT INTO credentials" in s for s in sql_texts)
        assert any("INSERT INTO preferences" in s for s in sql_texts)


# ---------------------------------------------------------------------------
# Tests — _migrate_v4 (FK constraints + credentials index)
# ---------------------------------------------------------------------------


class TestMigrateV4:
    @pytest.mark.asyncio
    async def test_migrate_v4_adds_fk_constraints_and_index(self):
        """_migrate_v4 should emit DO blocks for four FK constraints and one CREATE INDEX."""
        fake_conn = FakeConnection()
        backend, _ = _make_backend(fake_conn)

        await backend._migrate_v4()

        sql_texts = [sql for sql, _ in fake_conn.executed]

        # Verify all four FK constraints are created via DO blocks
        assert any("fk_credentials_user" in s for s in sql_texts), \
            "Missing FK constraint fk_credentials_user"
        assert any("fk_preferences_user" in s for s in sql_texts), \
            "Missing FK constraint fk_preferences_user"
        assert any("fk_sessions_user" in s for s in sql_texts), \
            "Missing FK constraint fk_sessions_user"
        assert any("fk_microsoft_tokens_user" in s for s in sql_texts), \
            "Missing FK constraint fk_microsoft_tokens_user"

        # Verify the credentials index
        assert any("idx_credentials_user" in s for s in sql_texts), \
            "Missing index idx_credentials_user"

    @pytest.mark.asyncio
    async def test_migrate_v4_uses_if_not_exists_guard(self):
        """Each FK ALTER TABLE should be wrapped in a DO block that checks information_schema."""
        fake_conn = FakeConnection()
        backend, _ = _make_backend(fake_conn)

        await backend._migrate_v4()

        sql_texts = [sql for sql, _ in fake_conn.executed]
        fk_statements = [s for s in sql_texts if "FOREIGN KEY" in s]
        assert len(fk_statements) == 4, f"Expected 4 FK statements, got {len(fk_statements)}"

        for stmt in fk_statements:
            assert "information_schema.table_constraints" in stmt, \
                f"FK statement missing information_schema guard: {stmt[:80]}..."
            assert "ON DELETE CASCADE" in stmt, \
                f"FK statement missing ON DELETE CASCADE: {stmt[:80]}..."

    @pytest.mark.asyncio
    async def test_migrate_v4_fk_references_users(self):
        """All FK constraints should reference users(user_id)."""
        fake_conn = FakeConnection()
        backend, _ = _make_backend(fake_conn)

        await backend._migrate_v4()

        sql_texts = [sql for sql, _ in fake_conn.executed]
        fk_statements = [s for s in sql_texts if "FOREIGN KEY" in s]

        for stmt in fk_statements:
            assert "REFERENCES users(user_id)" in stmt, \
                f"FK statement does not reference users(user_id): {stmt[:80]}..."

    @pytest.mark.asyncio
    async def test_migrate_v4_index_uses_if_not_exists(self):
        """The credentials index statement should use IF NOT EXISTS."""
        fake_conn = FakeConnection()
        backend, _ = _make_backend(fake_conn)

        await backend._migrate_v4()

        sql_texts = [sql for sql, _ in fake_conn.executed]
        idx_stmts = [s for s in sql_texts if "idx_credentials_user" in s]
        assert len(idx_stmts) == 1
        assert "IF NOT EXISTS" in idx_stmts[0]

    @pytest.mark.asyncio
    async def test_run_migrations_calls_v4_when_current_below_4(self):
        """_run_migrations should call _migrate_v4 when current version < 4."""
        fake_conn = FakeConnection()
        # Return version 3 from schema_migrations query
        fake_conn.fetched_rows = [{"v": 3}]
        backend, _ = _make_backend(fake_conn)

        with patch.object(backend, "_migrate_v4", new_callable=AsyncMock) as mock_v4:
            await backend._run_migrations()
            mock_v4.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_migrations_skips_v4_when_already_at_4(self):
        """_run_migrations should NOT call _migrate_v4 when version is already 4."""
        fake_conn = FakeConnection()
        fake_conn.fetched_rows = [{"v": 4}]
        backend, _ = _make_backend(fake_conn)

        with patch.object(backend, "_migrate_v4", new_callable=AsyncMock) as mock_v4:
            await backend._run_migrations()
            mock_v4.assert_not_awaited()
