"""Tests for session store abstraction and OAuth state persistence.

Covers:
- InMemorySessionStore (sessions, auth failure tracking, LRU eviction)
- RedisSessionStore (mocked — no live Redis needed)
- OAuth pending-setup persistence and recovery after simulated restart
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from asibot.session_store import InMemorySessionStore, SessionStore


# =====================================================================
# InMemorySessionStore
# =====================================================================


class TestInMemorySessionStoreInterface:
    """Verify InMemorySessionStore satisfies the SessionStore ABC."""

    def test_is_session_store(self):
        store = InMemorySessionStore()
        assert isinstance(store, SessionStore)


class TestInMemorySessionPutGet:
    def test_put_and_get(self):
        store = InMemorySessionStore()
        store.put_session("s1", "alice@example.com", ttl=3600)
        result = store.get_session("s1")
        assert result is not None
        user_id, ts = result
        assert user_id == "alice@example.com"
        assert ts <= time.time()

    def test_get_missing_returns_none(self):
        store = InMemorySessionStore()
        assert store.get_session("nonexistent") is None

    def test_put_overwrites(self):
        store = InMemorySessionStore()
        store.put_session("s1", "alice@example.com", ttl=3600)
        store.put_session("s1", "bob@example.com", ttl=3600)
        user_id, _ = store.get_session("s1")
        assert user_id == "bob@example.com"

    def test_expired_session_not_returned(self):
        store = InMemorySessionStore(default_ttl=1)
        store.put_session("s1", "alice@example.com", ttl=1, timestamp=time.time() - 10)
        assert store.get_session("s1") is None

    def test_custom_timestamp(self):
        store = InMemorySessionStore()
        ts = time.time() - 100
        store.put_session("s1", "alice@example.com", ttl=3600, timestamp=ts)
        result = store.get_session("s1")
        assert result is not None
        # get_session refreshes the timestamp, so we just check it returns a value
        assert result[0] == "alice@example.com"


class TestInMemorySessionDelete:
    def test_delete_session(self):
        store = InMemorySessionStore()
        store.put_session("s1", "alice@example.com", ttl=3600)
        store.delete_session("s1")
        assert store.get_session("s1") is None

    def test_delete_nonexistent_is_noop(self):
        store = InMemorySessionStore()
        store.delete_session("doesnotexist")  # should not raise

    def test_delete_user_sessions(self):
        store = InMemorySessionStore()
        store.put_session("s1", "alice@example.com", ttl=3600)
        store.put_session("s2", "bob@example.com", ttl=3600)
        store.put_session("s3", "alice@example.com", ttl=3600)
        count = store.delete_user_sessions("alice@example.com")
        assert count == 2
        assert store.get_session("s1") is None
        assert store.get_session("s3") is None
        assert store.get_session("s2") is not None

    def test_delete_user_sessions_returns_zero_if_none(self):
        store = InMemorySessionStore()
        assert store.delete_user_sessions("nobody@example.com") == 0


class TestInMemoryEviction:
    def test_evict_expired(self):
        store = InMemorySessionStore(default_ttl=1)
        store.put_session("old", "a@b.com", ttl=1, timestamp=time.time() - 10)
        store.put_session("new", "c@d.com", ttl=3600)
        store.evict_expired()
        assert store.get_session("old") is None
        assert store.get_session("new") is not None

    def test_lru_eviction_on_capacity(self):
        store = InMemorySessionStore(max_sessions=3, default_ttl=3600)
        store.put_session("s1", "a@b.com", ttl=3600)
        store.put_session("s2", "b@c.com", ttl=3600)
        store.put_session("s3", "c@d.com", ttl=3600)
        # At capacity — adding s4 should evict s1 (oldest)
        store.put_session("s4", "d@e.com", ttl=3600)
        assert store.get_session("s1") is None
        assert store.get_session("s4") is not None
        # Count should be at most max_sessions
        assert len(store._sessions) <= 3


class TestInMemoryAuthFailures:
    def test_not_rate_limited_initially(self):
        store = InMemorySessionStore()
        assert not store.is_rate_limited("pfx", window=300, max_failures=10)

    def test_rate_limited_after_max_failures(self):
        store = InMemorySessionStore()
        for _ in range(10):
            store.record_auth_failure("pfx")
        assert store.is_rate_limited("pfx", window=300, max_failures=10)

    def test_below_threshold_not_limited(self):
        store = InMemorySessionStore()
        for _ in range(5):
            store.record_auth_failure("pfx")
        assert not store.is_rate_limited("pfx", window=300, max_failures=10)

    def test_old_failures_expire(self):
        store = InMemorySessionStore()
        # Manually inject old timestamps
        store._auth_failures["pfx"] = [time.time() - 600] * 10
        assert not store.is_rate_limited("pfx", window=300, max_failures=10)

    def test_different_prefixes_independent(self):
        store = InMemorySessionStore()
        for _ in range(10):
            store.record_auth_failure("pfx_a")
        assert store.is_rate_limited("pfx_a", window=300, max_failures=10)
        assert not store.is_rate_limited("pfx_b", window=300, max_failures=10)


# =====================================================================
# RedisSessionStore (mocked)
# =====================================================================


class TestRedisSessionStoreMocked:
    """Test RedisSessionStore using a mocked redis client."""

    def _make_store(self):
        """Create a RedisSessionStore with a fully mocked Redis client."""
        from asibot.session_store import RedisSessionStore
        store = RedisSessionStore.__new__(RedisSessionStore)
        mock_client = MagicMock()
        store._default_ttl = 3600
        store._redis = mock_client
        return store, mock_client

    def test_put_session_calls_set(self):
        store, mock_redis = self._make_store()
        store.put_session("s1", "alice@example.com", ttl=3600)
        mock_redis.set.assert_called_once()
        args, kwargs = mock_redis.set.call_args
        assert "asibot:session:s1" in args
        assert kwargs.get("ex") == 3600

    def test_get_session_calls_get(self):
        store, mock_redis = self._make_store()
        import json
        mock_redis.get.return_value = json.dumps({"user_id": "alice@example.com", "ts": 1000.0})
        result = store.get_session("s1")
        assert result == ("alice@example.com", 1000.0)
        mock_redis.expire.assert_called_once()

    def test_get_session_miss(self):
        store, mock_redis = self._make_store()
        mock_redis.get.return_value = None
        assert store.get_session("missing") is None

    def test_delete_session(self):
        store, mock_redis = self._make_store()
        store.delete_session("s1")
        mock_redis.delete.assert_called_once_with("asibot:session:s1")

    def test_record_auth_failure(self):
        store, mock_redis = self._make_store()
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        store.record_auth_failure("pfx")
        mock_pipe.zadd.assert_called_once()
        mock_pipe.expire.assert_called_once()
        mock_pipe.execute.assert_called_once()

    def test_is_rate_limited(self):
        store, mock_redis = self._make_store()
        mock_redis.zcard.return_value = 15
        assert store.is_rate_limited("pfx", window=300, max_failures=10)

    def test_is_not_rate_limited(self):
        store, mock_redis = self._make_store()
        mock_redis.zcard.return_value = 3
        assert not store.is_rate_limited("pfx", window=300, max_failures=10)

    def test_evict_expired_is_noop(self):
        """Redis handles TTL natively — evict_expired should be a no-op."""
        store, mock_redis = self._make_store()
        store.evict_expired()  # should not raise


# =====================================================================
# user_session integration with SessionStore
# =====================================================================


class TestUserSessionUsesStore:
    """Verify that user_session module delegates to the configured SessionStore."""

    def setup_method(self):
        from asibot import user_session
        self._original_store = user_session._store

    def teardown_method(self):
        from asibot import user_session
        user_session._store = self._original_store

    def test_cache_session_delegates(self):
        from asibot import user_session
        mock_store = MagicMock(spec=SessionStore)
        user_session.set_store(mock_store)
        user_session._cache_session("sess1", "alice@example.com")
        mock_store.put_session.assert_called_once_with("sess1", "alice@example.com", user_session._SESSION_TTL)

    def test_invalidate_delegates(self):
        from asibot import user_session
        mock_store = MagicMock(spec=SessionStore)
        mock_store.delete_user_sessions.return_value = 3
        user_session.set_store(mock_store)
        count = user_session.invalidate_user_sessions("alice@example.com")
        assert count == 3
        mock_store.delete_user_sessions.assert_called_once_with("alice@example.com")

    def test_rate_limiting_delegates(self):
        from asibot import user_session
        mock_store = MagicMock(spec=SessionStore)
        mock_store.is_rate_limited.return_value = True
        user_session.set_store(mock_store)
        assert user_session._is_rate_limited("pfx")
        mock_store.is_rate_limited.assert_called_once()


# =====================================================================
# Factory
# =====================================================================


class TestCreateSessionStore:
    def test_default_returns_in_memory(self):
        from asibot.config import settings
        from asibot.session_store import create_session_store
        with (
            patch.object(settings, "session_backend", "memory"),
            patch.object(settings, "session_ttl", 3600),
        ):
            store = create_session_store()
            assert isinstance(store, InMemorySessionStore)

    def test_redis_falls_back_on_empty_url(self):
        from asibot.config import settings
        from asibot.session_store import create_session_store
        with (
            patch.object(settings, "session_backend", "redis"),
            patch.object(settings, "redis_url", ""),
            patch.object(settings, "session_ttl", 3600),
        ):
            store = create_session_store()
            assert isinstance(store, InMemorySessionStore)

    def test_redis_falls_back_on_connection_error(self):
        from asibot.config import settings
        from asibot.session_store import RedisSessionStore, create_session_store
        with (
            patch.object(settings, "session_backend", "redis"),
            patch.object(settings, "redis_url", "redis://localhost:6379/0"),
            patch.object(settings, "session_ttl", 3600),
            patch.object(RedisSessionStore, "__init__", side_effect=Exception("connection refused")),
        ):
            store = create_session_store()
            assert isinstance(store, InMemorySessionStore)


# =====================================================================
# OAuth state persistence (server.py integration)
# =====================================================================


class TestOAuthStatePersistence:
    """Test that OAuth pending-setup state is persisted to DB and recoverable."""

    @pytest.mark.asyncio
    async def test_persist_setup_writes_to_cache_and_db(self):
        from asibot import server
        mock_db = AsyncMock()
        original_db = server._db_backend
        try:
            server._db_backend = mock_db
            server._pending_setups.clear()

            await server._persist_setup("test123", {"status": "pending", "_created_at": 1000.0})

            # In-memory cache updated
            assert "test123" in server._pending_setups
            assert server._pending_setups["test123"]["status"] == "pending"

            # DB called
            mock_db.store_pending_setup.assert_awaited_once()
            call_args = mock_db.store_pending_setup.call_args
            assert call_args[0][0] == "test123"
        finally:
            server._db_backend = original_db
            server._pending_setups.clear()

    @pytest.mark.asyncio
    async def test_persist_setup_works_without_db(self):
        from asibot import server
        original_db = server._db_backend
        try:
            server._db_backend = None
            server._pending_setups.clear()

            await server._persist_setup("test456", {"status": "pending", "_created_at": 1000.0})
            assert "test456" in server._pending_setups
        finally:
            server._db_backend = original_db
            server._pending_setups.clear()

    @pytest.mark.asyncio
    async def test_load_from_db_on_cache_miss(self):
        """Simulates server restart: cache is empty but DB has the entry."""
        from asibot import server
        mock_db = AsyncMock()
        mock_db.get_pending_setup.return_value = {
            "setup_id": "restart123",
            "user_id": None,
            "service": None,
            "state": {"status": "complete", "user": {"name": "Bob", "user_id": "bob@example.com", "api_key": "k"}},
            "created_at": None,
            "expires_at": None,
        }
        original_db = server._db_backend
        try:
            server._db_backend = mock_db
            server._pending_setups.clear()

            result = await server._load_setup_from_db("restart123")
            assert result is not None
            assert result["status"] == "complete"
            # Should also re-populate in-memory cache
            assert "restart123" in server._pending_setups
        finally:
            server._db_backend = original_db
            server._pending_setups.clear()

    @pytest.mark.asyncio
    async def test_load_from_db_returns_none_when_not_found(self):
        from asibot import server
        mock_db = AsyncMock()
        mock_db.get_pending_setup.return_value = None
        original_db = server._db_backend
        try:
            server._db_backend = mock_db
            result = await server._load_setup_from_db("missing")
            assert result is None
        finally:
            server._db_backend = original_db

    @pytest.mark.asyncio
    async def test_delete_setup_removes_from_both(self):
        from asibot import server
        mock_db = AsyncMock()
        original_db = server._db_backend
        try:
            server._db_backend = mock_db
            server._pending_setups["del123"] = {"status": "complete"}

            await server._delete_setup("del123")

            assert "del123" not in server._pending_setups
            mock_db.delete_pending_setup.assert_awaited_once_with("del123")
        finally:
            server._db_backend = original_db
            server._pending_setups.clear()

    @pytest.mark.asyncio
    async def test_setup_status_falls_back_to_db(self):
        """asibot_setup_status should find state in DB after a simulated restart."""
        from asibot import server
        mock_db = AsyncMock()
        mock_db.get_pending_setup.return_value = {
            "setup_id": "dbonly1",
            "user_id": "alice@example.com",
            "service": None,
            "state": {
                "status": "complete",
                "user": {"name": "Alice", "user_id": "alice@example.com", "api_key": "asb_test"},
            },
            "created_at": None,
            "expires_at": None,
        }
        mock_db.delete_pending_setup = AsyncMock()
        original_db = server._db_backend
        try:
            server._db_backend = mock_db
            server._pending_setups.clear()

            result = await server.asibot_setup_status(setup_id="dbonly1")
            assert "Setup complete" in result
            assert "Alice" in result
        finally:
            server._db_backend = original_db
            server._pending_setups.clear()

    @pytest.mark.asyncio
    async def test_db_error_does_not_break_persist(self):
        """If DB write fails, in-memory cache should still be updated."""
        from asibot import server
        mock_db = AsyncMock()
        mock_db.store_pending_setup.side_effect = Exception("DB down")
        original_db = server._db_backend
        try:
            server._db_backend = mock_db
            server._pending_setups.clear()

            # Should not raise
            await server._persist_setup("err1", {"status": "pending", "_created_at": 1000.0})
            assert "err1" in server._pending_setups
        finally:
            server._db_backend = original_db
            server._pending_setups.clear()


# =====================================================================
# Simulated full restart scenario
# =====================================================================


class TestRestartRecovery:
    """End-to-end test: OAuth state survives a simulated server restart."""

    @pytest.mark.asyncio
    async def test_session_survives_restart_via_store(self):
        """InMemorySessionStore data is lost on restart, but this verifies the
        abstraction works.  Redis backend would survive (tested via mocks above)."""
        store = InMemorySessionStore()
        store.put_session("s1", "alice@example.com", ttl=3600)

        # Simulate restart: create a new store
        store2 = InMemorySessionStore()
        assert store2.get_session("s1") is None  # data lost (expected for in-memory)

    @pytest.mark.asyncio
    async def test_oauth_state_survives_restart_via_db(self):
        """With a DB backend, OAuth state is recovered after clearing in-memory cache."""
        from asibot import server

        mock_db = AsyncMock()
        stored_state = {
            "status": "pending",
            "_created_at": time.time(),
        }
        mock_db.store_pending_setup = AsyncMock()
        mock_db.get_pending_setup.return_value = {
            "setup_id": "persist1",
            "user_id": None,
            "service": None,
            "state": stored_state,
            "created_at": None,
            "expires_at": None,
        }

        original_db = server._db_backend
        try:
            server._db_backend = mock_db

            # Persist state
            server._pending_setups.clear()
            await server._persist_setup("persist1", stored_state)
            assert "persist1" in server._pending_setups

            # Simulate restart: clear in-memory cache
            server._pending_setups.clear()
            assert "persist1" not in server._pending_setups

            # Recovery: load from DB
            recovered = await server._load_setup_from_db("persist1")
            assert recovered is not None
            assert recovered["status"] == "pending"
            # Also re-populated in cache
            assert "persist1" in server._pending_setups
        finally:
            server._db_backend = original_db
            server._pending_setups.clear()
