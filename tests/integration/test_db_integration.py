"""Integration tests for the Asibot PostgreSQL database backend.

Requires a running PostgreSQL instance.  Configure via:

    ASIBOT_TEST_DATABASE_URL=postgresql://asibot:asibot@localhost:5432/asibot_test

Tests are skipped automatically when the database is unreachable.
Run only integration tests with:

    pytest -m integration tests/integration/
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid

import pytest

pytestmark = [pytest.mark.integration]


# ====================================================================
# 1. Schema creation
# ====================================================================


class TestSchemaCreation:
    """Verify that initialize() creates every expected table and index."""

    EXPECTED_TABLES = {
        "users",
        "credentials",
        "preferences",
        "sessions",
        "audit_log",
        "microsoft_tokens",
        "schema_migrations",
    }

    @pytest.mark.asyncio
    async def test_all_tables_exist(self, pool):
        rows = await pool.fetch(
            """
            SELECT tablename FROM pg_tables
            WHERE schemaname = 'public'
            """
        )
        table_names = {r["tablename"] for r in rows}
        assert self.EXPECTED_TABLES.issubset(table_names), (
            f"Missing tables: {self.EXPECTED_TABLES - table_names}"
        )

    @pytest.mark.asyncio
    async def test_users_primary_key(self, pool):
        rows = await pool.fetch(
            """
            SELECT column_name FROM information_schema.key_column_usage
            WHERE table_name = 'users'
              AND constraint_name = (
                  SELECT constraint_name FROM information_schema.table_constraints
                  WHERE table_name = 'users' AND constraint_type = 'PRIMARY KEY'
              )
            """
        )
        pk_cols = {r["column_name"] for r in rows}
        assert pk_cols == {"user_id"}

    @pytest.mark.asyncio
    async def test_credentials_composite_pk(self, pool):
        rows = await pool.fetch(
            """
            SELECT column_name FROM information_schema.key_column_usage
            WHERE table_name = 'credentials'
              AND constraint_name = (
                  SELECT constraint_name FROM information_schema.table_constraints
                  WHERE table_name = 'credentials' AND constraint_type = 'PRIMARY KEY'
              )
            """
        )
        pk_cols = {r["column_name"] for r in rows}
        assert pk_cols == {"user_id", "service"}

    @pytest.mark.asyncio
    async def test_unique_index_on_api_key(self, pool):
        rows = await pool.fetch(
            """
            SELECT indexname FROM pg_indexes
            WHERE tablename = 'users' AND indexname = 'idx_users_api_key'
            """
        )
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_audit_log_indexes(self, pool):
        rows = await pool.fetch(
            """
            SELECT indexname FROM pg_indexes
            WHERE tablename = 'audit_log'
            """
        )
        names = {r["indexname"] for r in rows}
        assert "idx_audit_ts" in names
        assert "idx_audit_user" in names
        assert "idx_audit_service" in names

    @pytest.mark.asyncio
    async def test_sessions_indexes(self, pool):
        rows = await pool.fetch(
            """
            SELECT indexname FROM pg_indexes
            WHERE tablename = 'sessions'
            """
        )
        names = {r["indexname"] for r in rows}
        assert "idx_sessions_user" in names
        assert "idx_sessions_expires" in names


# ====================================================================
# 2. User CRUD
# ====================================================================


class TestUserCRUD:
    """Test user create, read, update, list, and key rotation."""

    @pytest.mark.asyncio
    async def test_create_user(self, backend):
        ok = await backend.create_user(
            "alice@test.com", "Alice", "key-alice-001", "2025-01-01T00:00:00Z"
        )
        assert ok is True

    @pytest.mark.asyncio
    async def test_create_user_duplicate(self, backend):
        # Ensure user exists first.
        await backend.create_user(
            "dup@test.com", "Dup", "key-dup-001", "2025-01-01T00:00:00Z"
        )
        ok = await backend.create_user(
            "dup@test.com", "Dup Again", "key-dup-002", "2025-01-02T00:00:00Z"
        )
        assert ok is False

    @pytest.mark.asyncio
    async def test_get_user_by_email(self, backend):
        await backend.create_user(
            "bob@test.com", "Bob", "key-bob-001", "2025-01-01T00:00:00Z"
        )
        user = await backend.get_user_by_email("bob@test.com")
        assert user is not None
        assert user["user_id"] == "bob@test.com"
        assert user["name"] == "Bob"
        assert user["role"] == "user"

    @pytest.mark.asyncio
    async def test_get_user_by_email_missing(self, backend):
        user = await backend.get_user_by_email("nonexistent@test.com")
        assert user is None

    @pytest.mark.asyncio
    async def test_get_user_by_key(self, backend):
        await backend.create_user(
            "carol@test.com", "Carol", "key-carol-001", "2025-01-01T00:00:00Z"
        )
        user = await backend.get_user_by_key("key-carol-001")
        assert user is not None
        assert user["user_id"] == "carol@test.com"

    @pytest.mark.asyncio
    async def test_get_user_by_key_missing(self, backend):
        user = await backend.get_user_by_key("no-such-key")
        assert user is None

    @pytest.mark.asyncio
    async def test_list_users(self, backend):
        users = await backend.list_users()
        assert isinstance(users, list)
        # At least the users we created above should be present.
        emails = {u["user_id"] for u in users}
        assert "alice@test.com" in emails

    @pytest.mark.asyncio
    async def test_rotate_key(self, backend):
        await backend.create_user(
            "rotate@test.com", "Rotate", "key-rotate-old", "2025-01-01T00:00:00Z"
        )
        updated = await backend.rotate_key(
            "rotate@test.com", "key-rotate-new", "2025-06-01T00:00:00Z"
        )
        assert updated is not None
        assert updated["api_key"] == "key-rotate-new"
        assert updated["key_rotated_at"] == "2025-06-01T00:00:00Z"

        # Old key should no longer work.
        assert await backend.get_user_by_key("key-rotate-old") is None
        # New key should work.
        assert (await backend.get_user_by_key("key-rotate-new")) is not None

    @pytest.mark.asyncio
    async def test_set_role(self, backend):
        await backend.create_user(
            "roletest@test.com", "Role", "key-role-001", "2025-01-01T00:00:00Z"
        )
        updated = await backend.set_role("roletest@test.com", "admin")
        assert updated is not None
        assert updated["role"] == "admin"

    @pytest.mark.asyncio
    async def test_set_role_nonexistent_user(self, backend):
        result = await backend.set_role("nobody@test.com", "admin")
        assert result is None

    @pytest.mark.asyncio
    async def test_create_user_with_role(self, backend):
        ok = await backend.create_user(
            "adminuser@test.com", "Admin", "key-admin-001", "2025-01-01T00:00:00Z",
            role="admin",
        )
        assert ok is True
        user = await backend.get_user_by_email("adminuser@test.com")
        assert user["role"] == "admin"


# ====================================================================
# 3. Credential storage & retrieval
# ====================================================================


class TestCredentials:
    """Test encrypted credential blob round-trip."""

    USER_ID = "cred-user@test.com"
    BLOB = b"\x00\x01\x02encrypted-payload\xff\xfe"

    @pytest.fixture(autouse=True)
    async def _ensure_user(self, backend):
        await backend.create_user(
            self.USER_ID, "CredUser", f"key-cred-{uuid.uuid4().hex[:8]}",
            "2025-01-01T00:00:00Z",
        )

    @pytest.mark.asyncio
    async def test_set_and_get_credentials(self, backend):
        await backend.set_credentials(self.USER_ID, "github", self.BLOB)
        result = await backend.get_credentials(self.USER_ID, "github")
        assert result == self.BLOB

    @pytest.mark.asyncio
    async def test_get_missing_credentials(self, backend):
        result = await backend.get_credentials(self.USER_ID, "nonexistent-service")
        assert result is None

    @pytest.mark.asyncio
    async def test_update_credentials(self, backend):
        await backend.set_credentials(self.USER_ID, "salesforce", b"old-blob")
        await backend.set_credentials(self.USER_ID, "salesforce", b"new-blob")
        result = await backend.get_credentials(self.USER_ID, "salesforce")
        assert result == b"new-blob"

    @pytest.mark.asyncio
    async def test_remove_credentials(self, backend):
        await backend.set_credentials(self.USER_ID, "jira", b"jira-creds")
        await backend.remove_credentials(self.USER_ID, "jira")
        result = await backend.get_credentials(self.USER_ID, "jira")
        assert result is None

    @pytest.mark.asyncio
    async def test_list_connected(self, backend):
        await backend.set_credentials(self.USER_ID, "svc_a", b"a")
        await backend.set_credentials(self.USER_ID, "svc_b", b"b")
        connected = await backend.list_connected(self.USER_ID)
        assert "svc_a" in connected
        assert "svc_b" in connected

    @pytest.mark.asyncio
    async def test_set_credentials_with_prefs(self, backend):
        """set_credentials_with_prefs stores both credential and default prefs atomically."""
        await backend.set_credentials_with_prefs(
            self.USER_ID, "hubspot", b"hs-creds", enabled=True, mode="readwrite",
        )
        creds = await backend.get_credentials(self.USER_ID, "hubspot")
        assert creds == b"hs-creds"
        prefs = await backend.get_service_prefs(self.USER_ID, "hubspot")
        assert prefs["enabled"] is True
        assert prefs["mode"] == "readwrite"

    @pytest.mark.asyncio
    async def test_binary_blob_roundtrip(self, backend):
        """Ensure arbitrary binary data survives the round-trip (null bytes, high bytes)."""
        blob = bytes(range(256))
        await backend.set_credentials(self.USER_ID, "binary_test", blob)
        result = await backend.get_credentials(self.USER_ID, "binary_test")
        assert result == blob


# ====================================================================
# 4. Session persistence
# ====================================================================


class TestSessions:
    """Test session cache, load, and purge."""

    USER_ID = "session-user@test.com"

    @pytest.fixture(autouse=True)
    async def _ensure_user(self, backend):
        await backend.create_user(
            self.USER_ID, "SessionUser", f"key-sess-{uuid.uuid4().hex[:8]}",
            "2025-01-01T00:00:00Z",
        )

    @pytest.mark.asyncio
    async def test_cache_and_retrieve_session(self, backend):
        sid = f"sess-{uuid.uuid4().hex}"
        await backend.cache_session(sid, self.USER_ID, ttl=3600)
        uid = await backend.get_session_user(sid)
        assert uid == self.USER_ID

    @pytest.mark.asyncio
    async def test_expired_session_not_returned(self, backend):
        sid = f"sess-expired-{uuid.uuid4().hex}"
        # TTL of 0 means it expires immediately.
        await backend.cache_session(sid, self.USER_ID, ttl=0)
        uid = await backend.get_session_user(sid)
        assert uid is None

    @pytest.mark.asyncio
    async def test_load_active_sessions(self, backend):
        sid = f"sess-active-{uuid.uuid4().hex}"
        await backend.cache_session(sid, self.USER_ID, ttl=7200)
        active = await backend.load_active_sessions()
        assert sid in active
        uid, created = active[sid]
        assert uid == self.USER_ID
        assert isinstance(created, float)

    @pytest.mark.asyncio
    async def test_purge_expired_sessions(self, backend):
        # Insert an already-expired session.
        sid = f"sess-purge-{uuid.uuid4().hex}"
        await backend.cache_session(sid, self.USER_ID, ttl=0)
        deleted = await backend.purge_expired_sessions()
        assert deleted >= 1
        # Should no longer appear in active sessions.
        active = await backend.load_active_sessions()
        assert sid not in active

    @pytest.mark.asyncio
    async def test_delete_user_sessions(self, backend):
        sid1 = f"sess-del1-{uuid.uuid4().hex}"
        sid2 = f"sess-del2-{uuid.uuid4().hex}"
        await backend.cache_session(sid1, self.USER_ID, ttl=3600)
        await backend.cache_session(sid2, self.USER_ID, ttl=3600)
        deleted = await backend.delete_user_sessions(self.USER_ID)
        assert deleted >= 2
        assert await backend.get_session_user(sid1) is None
        assert await backend.get_session_user(sid2) is None

    @pytest.mark.asyncio
    async def test_session_upsert(self, backend):
        """Caching the same session_id again should update, not fail."""
        sid = f"sess-upsert-{uuid.uuid4().hex}"
        await backend.cache_session(sid, self.USER_ID, ttl=60)
        # Re-cache with a longer TTL.
        await backend.cache_session(sid, self.USER_ID, ttl=9999)
        uid = await backend.get_session_user(sid)
        assert uid == self.USER_ID


# ====================================================================
# 5. Audit log
# ====================================================================


class TestAuditLog:
    """Test audit log_audit, log_event, query, range query, and prune."""

    USER_ID = "audit-user@test.com"

    @pytest.fixture(autouse=True)
    async def _ensure_user(self, backend):
        await backend.create_user(
            self.USER_ID, "AuditUser", f"key-audit-{uuid.uuid4().hex[:8]}",
            "2025-01-01T00:00:00Z",
        )

    @pytest.mark.asyncio
    async def test_log_audit_and_query(self, backend):
        before = time.time()
        await backend.log_audit(
            self.USER_ID, "search_jira", '{"q": "bug"}',
            service="jira", success=True, latency_ms=42.5,
        )
        entries = await backend.query_audit(user_id=self.USER_ID, tool="search_jira")
        assert len(entries) >= 1
        entry = entries[0]
        assert entry["tool"] == "search_jira"
        assert entry["user_id"] == self.USER_ID

    @pytest.mark.asyncio
    async def test_log_event(self, backend):
        await backend.log_event(
            self.USER_ID, "session_start", service="github",
            metadata={"ip": "127.0.0.1"},
        )
        entries = await backend.query_audit(user_id=self.USER_ID, limit=10)
        events = [e for e in entries if e.get("event") == "session_start"]
        assert len(events) >= 1

    @pytest.mark.asyncio
    async def test_query_audit_with_since(self, backend):
        since = time.time()
        await backend.log_audit(self.USER_ID, "recent_tool", None)
        entries = await backend.query_audit(user_id=self.USER_ID, since=since)
        tools = [e["tool"] for e in entries if e.get("tool")]
        assert "recent_tool" in tools

    @pytest.mark.asyncio
    async def test_query_audit_range(self, backend):
        t1 = time.time()
        await backend.log_audit(self.USER_ID, "range_tool", None)
        t2 = time.time()
        results = await backend.query_audit_range(t1 - 1, t2 + 1)
        tools = [r.get("tool") for r in results]
        assert "range_tool" in tools

    @pytest.mark.asyncio
    async def test_log_audit_error_type(self, backend):
        await backend.log_audit(
            self.USER_ID, "failing_tool", None,
            service="salesforce", success=False, latency_ms=100.0,
            error_type="TimeoutError",
        )
        entries = await backend.query_audit(user_id=self.USER_ID, tool="failing_tool")
        assert len(entries) >= 1
        assert entries[0]["error_type"] == "TimeoutError"

    @pytest.mark.asyncio
    async def test_prune_audit(self, backend, pool):
        """Prune should delete entries older than max_age_days."""
        # Insert a very old audit entry directly.
        old_ts = time.time() - (365 * 86400)  # 1 year ago
        await pool.execute(
            """
            INSERT INTO audit_log (ts, user_id, tool, args_json)
            VALUES ($1, $2, $3, $4)
            """,
            old_ts, self.USER_ID, "ancient_tool", None,
        )
        deleted = await backend.prune_audit(max_age_days=90)
        assert deleted >= 1


# ====================================================================
# 6. Migration execution (v1 through v4)
# ====================================================================


class TestMigrations:
    """Verify that migrations applied the expected schema changes."""

    @pytest.mark.asyncio
    async def test_migration_version_recorded(self, pool):
        row = await pool.fetchrow(
            "SELECT MAX(version) AS v FROM schema_migrations"
        )
        assert row["v"] == 4

    @pytest.mark.asyncio
    async def test_v2_audit_columns_exist(self, pool):
        rows = await pool.fetch(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'audit_log'
            """
        )
        cols = {r["column_name"] for r in rows}
        for expected in ("service", "success", "latency_ms", "error_type", "event", "metadata_json"):
            assert expected in cols, f"v2 column '{expected}' missing from audit_log"

    @pytest.mark.asyncio
    async def test_v3_role_column_exists(self, pool):
        rows = await pool.fetch(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'users' AND column_name = 'role'
            """
        )
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_v4_foreign_keys_exist(self, pool):
        rows = await pool.fetch(
            """
            SELECT constraint_name FROM information_schema.table_constraints
            WHERE constraint_type = 'FOREIGN KEY'
              AND table_schema = 'public'
            """
        )
        fk_names = {r["constraint_name"] for r in rows}
        assert "fk_credentials_user" in fk_names
        assert "fk_preferences_user" in fk_names
        assert "fk_sessions_user" in fk_names
        assert "fk_microsoft_tokens_user" in fk_names

    @pytest.mark.asyncio
    async def test_v4_credentials_index(self, pool):
        rows = await pool.fetch(
            """
            SELECT indexname FROM pg_indexes
            WHERE tablename = 'credentials' AND indexname = 'idx_credentials_user'
            """
        )
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_idempotent_reinitialize(self, backend):
        """Calling initialize() a second time should not error (idempotent DDL)."""
        # Re-initialize on the same pool. The schema uses IF NOT EXISTS and
        # migrations check current version, so this must be safe.
        await backend.initialize()
        # Verify the backend is still functional.
        users = await backend.list_users()
        assert isinstance(users, list)


# ====================================================================
# 7. Transaction rollback (atomicity of set_role_with_audit)
# ====================================================================


class TestTransactionRollback:
    """Verify that set_role_with_audit is atomic: both writes commit or neither does."""

    @pytest.mark.asyncio
    async def test_set_role_with_audit_commits(self, backend):
        uid = f"txn-ok-{uuid.uuid4().hex[:8]}@test.com"
        await backend.create_user(uid, "TxnOk", f"key-txnok-{uuid.uuid4().hex[:8]}", "2025-01-01T00:00:00Z")

        result = await backend.set_role_with_audit(uid, "admin", admin_id="super@test.com")
        assert result is not None
        assert result["role"] == "admin"

        # Audit entry should exist.
        entries = await backend.query_audit(user_id="super@test.com", limit=10)
        role_events = [
            e for e in entries
            if e.get("event") == "role_changed"
        ]
        assert len(role_events) >= 1

    @pytest.mark.asyncio
    async def test_set_role_with_audit_nonexistent_rolls_back(self, backend, pool):
        """If the user does not exist, no audit row should be written."""
        ts_before = time.time()
        result = await backend.set_role_with_audit(
            "ghost@test.com", "admin", admin_id="admin@test.com"
        )
        assert result is None

        # No audit entry for this specific event should exist after ts_before.
        rows = await pool.fetch(
            """
            SELECT * FROM audit_log
            WHERE event = 'role_changed' AND ts >= $1
              AND metadata_json LIKE '%ghost@test.com%'
            """,
            ts_before,
        )
        assert len(rows) == 0

    @pytest.mark.asyncio
    async def test_transaction_rollback_on_error(self, backend, pool):
        """Demonstrate that an exception inside a transaction block rolls back all changes."""
        uid = f"txn-rb-{uuid.uuid4().hex[:8]}@test.com"
        await backend.create_user(uid, "TxnRollback", f"key-txnrb-{uuid.uuid4().hex[:8]}", "2025-01-01T00:00:00Z")

        with pytest.raises(RuntimeError):
            async with backend.transaction() as conn:
                await conn.execute(
                    "UPDATE users SET role = 'admin' WHERE user_id = $1", uid
                )
                # Force an error to trigger rollback.
                raise RuntimeError("deliberate failure")

        # Role should still be the original value ('user'), not 'admin'.
        user = await backend.get_user_by_email(uid)
        assert user["role"] == "user"


# ====================================================================
# 8. Connection pool under concurrent load
# ====================================================================


class TestConnectionPoolConcurrency:
    """Verify the pool handles concurrent operations gracefully."""

    @pytest.mark.asyncio
    async def test_10_parallel_user_creates(self, backend):
        """10 parallel create_user calls should all succeed without pool exhaustion."""
        async def _create(i: int) -> bool:
            uid = f"pool-{i}-{uuid.uuid4().hex[:6]}@test.com"
            return await backend.create_user(
                uid, f"Pool{i}", f"key-pool-{uuid.uuid4().hex}", "2025-01-01T00:00:00Z"
            )

        results = await asyncio.gather(*[_create(i) for i in range(10)])
        assert all(r is True for r in results)

    @pytest.mark.asyncio
    async def test_10_parallel_credential_ops(self, backend):
        """10 parallel set + get credential round-trips."""
        base_uid = f"pool-cred-{uuid.uuid4().hex[:6]}@test.com"
        await backend.create_user(
            base_uid, "PoolCred", f"key-poolcred-{uuid.uuid4().hex}", "2025-01-01T00:00:00Z"
        )

        async def _roundtrip(i: int) -> bool:
            svc = f"service_{i}"
            blob = f"blob-{i}".encode()
            await backend.set_credentials(base_uid, svc, blob)
            result = await backend.get_credentials(base_uid, svc)
            return result == blob

        results = await asyncio.gather(*[_roundtrip(i) for i in range(10)])
        assert all(results)

    @pytest.mark.asyncio
    async def test_10_parallel_audit_writes(self, backend):
        """10 parallel audit log writes should not deadlock or error."""
        async def _log(i: int) -> None:
            await backend.log_audit(
                f"conc-user-{i}@test.com", f"tool_{i}", f'{{"i": {i}}}',
                service="test", success=True, latency_ms=float(i),
            )

        await asyncio.gather(*[_log(i) for i in range(10)])
        # No exception = pass; verify at least some entries exist.
        entries = await backend.query_audit(tool="tool_0", limit=5)
        assert len(entries) >= 1

    @pytest.mark.asyncio
    async def test_10_parallel_session_ops(self, backend):
        """10 parallel session cache + lookup operations."""
        uid = f"pool-sess-{uuid.uuid4().hex[:6]}@test.com"
        await backend.create_user(
            uid, "PoolSess", f"key-poolsess-{uuid.uuid4().hex}", "2025-01-01T00:00:00Z"
        )

        async def _session_op(i: int) -> bool:
            sid = f"sess-conc-{i}-{uuid.uuid4().hex}"
            await backend.cache_session(sid, uid, ttl=3600)
            result = await backend.get_session_user(sid)
            return result == uid

        results = await asyncio.gather(*[_session_op(i) for i in range(10)])
        assert all(results)

    @pytest.mark.asyncio
    async def test_mixed_concurrent_operations(self, backend):
        """Mix of different operation types running concurrently."""
        uid = f"pool-mix-{uuid.uuid4().hex[:6]}@test.com"
        await backend.create_user(
            uid, "PoolMix", f"key-poolmix-{uuid.uuid4().hex}", "2025-01-01T00:00:00Z"
        )

        async def _write_cred():
            await backend.set_credentials(uid, "mix_svc", b"data")
            return True

        async def _write_session():
            await backend.cache_session(f"mix-sess-{uuid.uuid4().hex}", uid, ttl=300)
            return True

        async def _write_audit():
            await backend.log_audit(uid, "mix_tool", None, service="mix")
            return True

        async def _read_user():
            return (await backend.get_user_by_email(uid)) is not None

        async def _list_users():
            return len(await backend.list_users()) > 0

        tasks = [
            _write_cred(), _write_session(), _write_audit(),
            _read_user(), _list_users(),
            _write_cred(), _write_session(), _write_audit(),
            _read_user(), _list_users(),
        ]
        results = await asyncio.gather(*tasks)
        assert all(results)


# ====================================================================
# 9. FK cascade (delete user -> cascade to credentials, sessions, prefs)
# ====================================================================


class TestForeignKeyCascade:
    """Verify ON DELETE CASCADE removes child rows when a user is deleted."""

    @pytest.mark.asyncio
    async def test_delete_user_cascades_credentials(self, backend, pool):
        uid = f"cascade-{uuid.uuid4().hex[:8]}@test.com"
        api_key = f"key-cascade-{uuid.uuid4().hex}"
        await backend.create_user(uid, "Cascade", api_key, "2025-01-01T00:00:00Z")

        # Add credentials.
        await backend.set_credentials(uid, "github", b"gh-creds")
        await backend.set_credentials(uid, "jira", b"jira-creds")

        # Add preferences.
        await backend.set_service_prefs(uid, "github", True, "readwrite")

        # Add a session.
        sid = f"cascade-sess-{uuid.uuid4().hex}"
        await backend.cache_session(sid, uid, ttl=3600)

        # Add a Microsoft token.
        await backend.save_ms_token(uid, b"ms-token-data")

        # Delete the user directly.
        await pool.execute("DELETE FROM users WHERE user_id = $1", uid)

        # Verify cascading deletes.
        creds = await pool.fetch(
            "SELECT * FROM credentials WHERE user_id = $1", uid
        )
        assert len(creds) == 0, "Credentials should be cascade-deleted"

        prefs = await pool.fetch(
            "SELECT * FROM preferences WHERE user_id = $1", uid
        )
        assert len(prefs) == 0, "Preferences should be cascade-deleted"

        sessions = await pool.fetch(
            "SELECT * FROM sessions WHERE user_id = $1", uid
        )
        assert len(sessions) == 0, "Sessions should be cascade-deleted"

        ms_tokens = await pool.fetch(
            "SELECT * FROM microsoft_tokens WHERE user_id = $1", uid
        )
        assert len(ms_tokens) == 0, "Microsoft tokens should be cascade-deleted"

    @pytest.mark.asyncio
    async def test_fk_prevents_orphan_credential(self, backend, pool):
        """Inserting a credential for a non-existent user should fail with FK violation."""
        import asyncpg

        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await pool.execute(
                """
                INSERT INTO credentials (user_id, service, encrypted_blob)
                VALUES ($1, $2, $3)
                """,
                "no-such-user@test.com",
                "github",
                b"creds",
            )

    @pytest.mark.asyncio
    async def test_fk_prevents_orphan_session(self, backend, pool):
        """Inserting a session for a non-existent user should fail with FK violation."""
        import asyncpg

        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await pool.execute(
                """
                INSERT INTO sessions (session_id, user_id, created_at, expires_at)
                VALUES ($1, $2, $3, $4)
                """,
                "orphan-sess",
                "no-such-user@test.com",
                time.time(),
                time.time() + 3600,
            )


# ====================================================================
# 10. Database stats
# ====================================================================


class TestDatabaseStats:
    """Verify db_stats returns the expected structure."""

    @pytest.mark.asyncio
    async def test_db_stats_structure(self, backend):
        stats = await backend.db_stats()
        assert isinstance(stats, dict)
        for key in (
            "users_count",
            "credentials_count",
            "sessions_count",
            "audit_log_count",
            "microsoft_tokens_count",
            "pool_size",
            "pool_free",
            "pool_min",
            "pool_max",
        ):
            assert key in stats, f"Missing stat key: {key}"
            assert isinstance(stats[key], (int, float))

    @pytest.mark.asyncio
    async def test_db_stats_counts_are_nonnegative(self, backend):
        stats = await backend.db_stats()
        assert stats["users_count"] >= 0
        assert stats["pool_size"] >= 0
        assert stats["pool_max"] >= stats["pool_min"]


# ====================================================================
# 11. Microsoft token storage
# ====================================================================


class TestMicrosoftTokens:
    """Test Microsoft token save/load round-trip."""

    @pytest.mark.asyncio
    async def test_save_and_load_ms_token(self, backend):
        uid = f"ms-token-{uuid.uuid4().hex[:8]}@test.com"
        await backend.create_user(
            uid, "MSUser", f"key-ms-{uuid.uuid4().hex}", "2025-01-01T00:00:00Z"
        )
        token_blob = b"encrypted-ms-refresh-token-data"
        await backend.save_ms_token(uid, token_blob)
        loaded = await backend.load_ms_token(uid)
        assert loaded == token_blob

    @pytest.mark.asyncio
    async def test_load_missing_ms_token(self, backend):
        uid = f"ms-notoken-{uuid.uuid4().hex[:8]}@test.com"
        await backend.create_user(
            uid, "MSNoToken", f"key-msno-{uuid.uuid4().hex}", "2025-01-01T00:00:00Z"
        )
        loaded = await backend.load_ms_token(uid)
        assert loaded is None

    @pytest.mark.asyncio
    async def test_update_ms_token(self, backend):
        uid = f"ms-update-{uuid.uuid4().hex[:8]}@test.com"
        await backend.create_user(
            uid, "MSUpdate", f"key-msup-{uuid.uuid4().hex}", "2025-01-01T00:00:00Z"
        )
        await backend.save_ms_token(uid, b"old-token")
        await backend.save_ms_token(uid, b"new-token")
        loaded = await backend.load_ms_token(uid)
        assert loaded == b"new-token"


# ====================================================================
# 12. Preferences
# ====================================================================


class TestPreferences:
    """Test service preferences CRUD."""

    @pytest.mark.asyncio
    async def test_set_and_get_prefs(self, backend):
        uid = f"pref-{uuid.uuid4().hex[:8]}@test.com"
        await backend.create_user(
            uid, "PrefUser", f"key-pref-{uuid.uuid4().hex}", "2025-01-01T00:00:00Z"
        )
        await backend.set_service_prefs(uid, "github", True, "readwrite")
        prefs = await backend.get_service_prefs(uid, "github")
        assert prefs == {"enabled": True, "mode": "readwrite"}

    @pytest.mark.asyncio
    async def test_get_missing_prefs(self, backend):
        uid = f"nopref-{uuid.uuid4().hex[:8]}@test.com"
        await backend.create_user(
            uid, "NoPref", f"key-nopref-{uuid.uuid4().hex}", "2025-01-01T00:00:00Z"
        )
        prefs = await backend.get_service_prefs(uid, "nonexistent")
        assert prefs == {}

    @pytest.mark.asyncio
    async def test_update_prefs(self, backend):
        uid = f"uppref-{uuid.uuid4().hex[:8]}@test.com"
        await backend.create_user(
            uid, "UpPref", f"key-uppref-{uuid.uuid4().hex}", "2025-01-01T00:00:00Z"
        )
        await backend.set_service_prefs(uid, "jira", True, "read")
        await backend.set_service_prefs(uid, "jira", False, "readwrite")
        prefs = await backend.get_service_prefs(uid, "jira")
        assert prefs == {"enabled": False, "mode": "readwrite"}
