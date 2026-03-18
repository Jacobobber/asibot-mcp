"""Tests for user session management, sanitization, and cache eviction."""

import sqlite3
import time
from unittest.mock import MagicMock, patch

import pytest

from asibot import user_session
from asibot.config import settings
from asibot.session_store import InMemorySessionStore


def _mock_ctx(api_key=None, session_id=None):
    """Create a mock MCP Context."""
    ctx = MagicMock()
    header_data = {}
    if api_key:
        header_data["authorization"] = f"Bearer {api_key}"
    if session_id:
        header_data["mcp-session-id"] = session_id
    headers = MagicMock()
    headers.get = MagicMock(side_effect=lambda k, d="": header_data.get(k, d))
    ctx.request_context.request.headers = headers
    return ctx


def _fresh_store() -> InMemorySessionStore:
    """Create and install a fresh InMemorySessionStore for test isolation."""
    store = InMemorySessionStore()
    user_session.set_store(store)
    return store


class TestSanitizeUserId:
    def test_valid_email(self):
        result = user_session._sanitize_user_id("alice@example.com")
        assert result == "alice_at_example.com"

    def test_valid_email_with_plus(self):
        result = user_session._sanitize_user_id("alice+tag@example.com")
        assert result == "alice+tag_at_example.com"

    def test_rejects_path_traversal(self):
        with pytest.raises(ValueError, match="Invalid user ID"):
            user_session._sanitize_user_id("../../etc/passwd")

    def test_rejects_path_traversal_with_at(self):
        with pytest.raises(ValueError, match="Invalid user ID"):
            user_session._sanitize_user_id("../admin@evil.com")

    def test_rejects_slash_in_email(self):
        with pytest.raises(ValueError, match="Invalid user ID"):
            user_session._sanitize_user_id("user/admin@example.com")

    def test_rejects_empty_string(self):
        with pytest.raises(ValueError, match="Invalid user ID"):
            user_session._sanitize_user_id("")

    def test_rejects_no_domain(self):
        with pytest.raises(ValueError, match="Invalid user ID"):
            user_session._sanitize_user_id("nodomain")

    def test_rejects_backslash(self):
        with pytest.raises(ValueError, match="Invalid user ID"):
            user_session._sanitize_user_id("user\\admin@example.com")


class TestGetUserDataDir:
    def test_creates_directory(self, tmp_path):
        with patch.object(settings, "data_dir", tmp_path):
            d = user_session.get_user_data_dir("test@example.com")
            assert d.exists()
            assert d.name == "test_at_example.com"

    def test_rejects_traversal(self, tmp_path):
        with patch.object(settings, "data_dir", tmp_path):
            with pytest.raises(ValueError):
                user_session.get_user_data_dir("../../etc/passwd")


class TestSessionCache:
    def setup_method(self):
        self.store = _fresh_store()

    def teardown_method(self):
        user_session.set_store(InMemorySessionStore())

    def test_session_cached_with_timestamp(self):
        self.store.put_session("sess1", "user@example.com", ttl=3600)
        result = self.store.get_session("sess1")
        assert result is not None
        uid, ts = result
        assert uid == "user@example.com"

    def test_evict_stale_sessions(self):
        now = time.time()
        self.store.put_session("fresh", "a@b.com", ttl=3600, timestamp=now)
        self.store.put_session("stale", "c@d.com", ttl=3600, timestamp=now - 7200)
        self.store.evict_expired()
        assert self.store.get_session("fresh") is not None
        assert self.store.get_session("stale") is None

    def test_cache_session_enforces_hard_cap(self):
        """_cache_session should evict LRU entry when at max capacity."""
        store = InMemorySessionStore(max_sessions=3)
        user_session.set_store(store)
        now = time.time()
        store.put_session("s1", "a@b.com", ttl=3600, timestamp=now - 100)
        store.put_session("s2", "b@c.com", ttl=3600, timestamp=now - 50)
        store.put_session("s3", "c@d.com", ttl=3600, timestamp=now - 10)
        # At capacity -- adding s4 should evict s1 (LRU / oldest)
        user_session._cache_session("s4", "d@e.com")
        assert store.get_session("s1") is None
        assert store.get_session("s4") is not None
        assert len(store._sessions) == 3

    def test_lru_eviction_order(self):
        """Accessing a session should move it to end, protecting it from eviction."""
        store = InMemorySessionStore(max_sessions=3)
        user_session.set_store(store)
        now = time.time()
        store.put_session("s1", "a@b.com", ttl=3600, timestamp=now - 100)
        store.put_session("s2", "b@c.com", ttl=3600, timestamp=now - 50)
        store.put_session("s3", "c@d.com", ttl=3600, timestamp=now - 10)
        # Access s1 -- moves it to end (most recently used)
        store.get_session("s1")
        # Now s2 is the LRU -- adding s4 should evict s2, not s1
        user_session._cache_session("s4", "d@e.com")
        assert store.get_session("s1") is not None  # protected by access
        assert store.get_session("s2") is None  # evicted as LRU
        assert store.get_session("s4") is not None

    def test_invalidate_user_sessions(self):
        """invalidate_user_sessions should remove all sessions for a given user."""
        self.store.put_session("s1", "alice@example.com", ttl=3600)
        self.store.put_session("s2", "bob@example.com", ttl=3600)
        self.store.put_session("s3", "alice@example.com", ttl=3600)
        count = user_session.invalidate_user_sessions("alice@example.com")
        assert count == 2
        assert self.store.get_session("s1") is None
        assert self.store.get_session("s3") is None
        assert self.store.get_session("s2") is not None  # bob unaffected

    def test_invalidate_no_sessions(self):
        """invalidate_user_sessions on non-existent user returns 0."""
        count = user_session.invalidate_user_sessions("nobody@example.com")
        assert count == 0

    def test_expired_session_not_returned(self):
        # Pre-populate with an expired session
        self.store.put_session("expired-sess", "old@example.com", ttl=3600, timestamp=time.time() - 7200)

        ctx = MagicMock()
        headers = MagicMock()
        headers.get = MagicMock(side_effect=lambda k, d="": {"mcp-session-id": "expired-sess"}.get(k, d))
        ctx.request_context.request.headers = headers

        with (
            patch.object(user_session.auth, "list_users", return_value=[]),
            patch.object(user_session, "_db_lookup_session", return_value=None),
        ):
            uid, err = user_session.require_user(ctx)
            assert uid is None
            assert "No users" in err


class TestRateLimiting:
    def setup_method(self):
        self.store = _fresh_store()

    def teardown_method(self):
        user_session.set_store(InMemorySessionStore())

    def test_not_rate_limited_initially(self):
        assert not user_session._is_rate_limited("test_pfx")

    def test_rate_limited_after_max_failures(self):
        for _ in range(user_session._AUTH_FAIL_MAX):
            self.store.record_auth_failure("test_pfx")
        assert user_session._is_rate_limited("test_pfx")

    def test_old_failures_pruned(self):
        # Manually inject old timestamps
        self.store._auth_failures["test_pfx"] = [
            time.time() - user_session._AUTH_FAIL_WINDOW - 1
        ] * user_session._AUTH_FAIL_MAX
        # Old failures should not count
        assert not user_session._is_rate_limited("test_pfx")

    def test_invalid_api_key_records_failure(self):
        ctx = _mock_ctx(api_key="bad_key_123")
        with (
            patch.object(user_session.auth, "get_user_by_key", return_value=None),
            patch.object(user_session.auth, "list_users", return_value=[]),
            patch.object(user_session, "_db_lookup_session", return_value=None),
        ):
            uid, err = user_session.require_user(ctx)
            assert uid is None
            assert "Invalid API key" in err
            key_pfx = user_session._key_prefix("bad_key_123")
            assert len(self.store._auth_failures.get(key_pfx, [])) == 1

    def test_rate_limited_response(self):
        # Fill up the failure list for a specific key prefix
        key_pfx = user_session._key_prefix("any_key_456")
        for _ in range(user_session._AUTH_FAIL_MAX):
            self.store.record_auth_failure(key_pfx)

        ctx = _mock_ctx(api_key="any_key_456")
        uid, err = user_session.require_user(ctx)
        assert uid is None
        assert "Too many failed" in err

    def test_different_keys_independent(self):
        """Rate limiting for one key should not affect another."""
        pfx_a = user_session._key_prefix("key_aaaa")
        pfx_b = user_session._key_prefix("key_bbbb")
        for _ in range(user_session._AUTH_FAIL_MAX):
            self.store.record_auth_failure(pfx_a)

        assert user_session._is_rate_limited(pfx_a)
        assert not user_session._is_rate_limited(pfx_b)


class TestSingleUserAutoLogin:
    def setup_method(self):
        _fresh_store()

    def teardown_method(self):
        user_session.set_store(InMemorySessionStore())

    def test_auto_login_allowed_on_stdio(self):
        """Single-user auto-login should work on stdio transport."""
        ctx = _mock_ctx()  # no API key
        mock_user = {"user_id": "sole@example.com", "name": "Solo", "api_key": "key"}
        with (
            patch.object(settings, "transport", "stdio"),
            patch.object(user_session.auth, "list_users", return_value=[mock_user]),
            patch.object(user_session, "_db_lookup_session", return_value=None),
        ):
            uid, err = user_session.require_user(ctx)
            assert uid == "sole@example.com"
            assert err is None

    def test_auto_login_blocked_on_http(self):
        """Single-user auto-login must NOT work on HTTP transport."""
        ctx = _mock_ctx()  # no API key
        mock_user = {"user_id": "sole@example.com", "name": "Solo", "api_key": "key"}
        with (
            patch.object(settings, "transport", "streamable-http"),
            patch.object(user_session.auth, "list_users", return_value=[mock_user]),
            patch.object(user_session, "_db_lookup_session", return_value=None),
        ):
            uid, err = user_session.require_user(ctx)
            assert uid is None
            assert "Authentication required" in err


class TestRequireUser:
    def setup_method(self):
        _fresh_store()

    def teardown_method(self):
        user_session.set_store(InMemorySessionStore())

    def test_valid_api_key_caches_session(self):
        ctx = MagicMock()
        header_data = {
            "authorization": "Bearer test_key_123",
            "mcp-session-id": "sess-abc",
        }
        headers = MagicMock()
        headers.get = MagicMock(side_effect=lambda k, d="": header_data.get(k, d))
        ctx.request_context.request.headers = headers

        mock_user = {"user_id": "test@example.com", "name": "Test", "api_key": "test_key_123"}
        with patch.object(user_session.auth, "get_user_by_key", return_value=mock_user):
            uid, err = user_session.require_user(ctx)
            assert uid == "test@example.com"
            assert err is None
            # Verify it was cached in the store
            store = user_session._get_store()
            result = store.get_session("sess-abc")
            assert result is not None
            cached_uid, _ = result
            assert cached_uid == "test@example.com"


class TestSessionTTLConfig:
    """session_ttl should be read from settings instead of hardcoded."""

    def setup_method(self):
        user_session._session_to_user.clear()
        user_session._auth_failures.clear()

    def teardown_method(self):
        user_session._session_to_user.clear()
        user_session._auth_failures.clear()

    def test_custom_ttl_evicts_stale(self):
        """Sessions older than settings.session_ttl should be evicted."""
        now = time.time()
        with patch.object(settings, "session_ttl", 60):
            user_session._session_to_user["fresh"] = ("a@b.com", now - 30)
            user_session._session_to_user["stale"] = ("c@d.com", now - 120)
            user_session._evict_stale_sessions()
            assert "fresh" in user_session._session_to_user
            assert "stale" not in user_session._session_to_user

    def test_custom_ttl_in_require_user(self):
        """require_user should respect settings.session_ttl for session expiry."""
        now = time.time()
        # Session is 90s old; default TTL=3600 would keep it, custom TTL=60 expires it
        user_session._session_to_user["sess-x"] = ("user@example.com", now - 90)

        ctx = _mock_ctx(session_id="sess-x")
        with (
            patch.object(settings, "session_ttl", 60),
            patch.object(user_session.auth, "list_users", return_value=[]),
            patch.object(user_session, "_db_lookup_session", return_value=None),
        ):
            uid, err = user_session.require_user(ctx)
            assert uid is None  # expired with TTL=60


class TestDBFallback:
    """Test that require_user falls back to the database when session not in memory."""

    def setup_method(self):
        user_session._session_to_user.clear()
        user_session._auth_failures.clear()

    def teardown_method(self):
        user_session._session_to_user.clear()
        user_session._auth_failures.clear()

    def test_db_fallback_when_not_in_memory(self):
        """If session not in memory, check DB and restore to cache."""
        ctx = _mock_ctx(session_id="db-sess-1")
        with patch.object(user_session, "_db_lookup_session", return_value="alice@example.com"):
            uid, err = user_session.require_user(ctx)
            assert uid == "alice@example.com"
            assert err is None
            # Should now be in memory cache
            assert "db-sess-1" in user_session._session_to_user
            cached_uid, _ = user_session._session_to_user["db-sess-1"]
            assert cached_uid == "alice@example.com"

    def test_db_fallback_returns_none_when_not_found(self):
        """If session not in memory or DB, proceed to API key auth."""
        ctx = _mock_ctx(session_id="unknown-sess", api_key="valid_key_000")
        mock_user = {"user_id": "bob@example.com", "name": "Bob"}
        with (
            patch.object(user_session, "_db_lookup_session", return_value=None),
            patch.object(user_session.auth, "get_user_by_key", return_value=mock_user),
        ):
            uid, err = user_session.require_user(ctx)
            assert uid == "bob@example.com"
            assert err is None

    def test_session_survives_cache_clear(self, tmp_path):
        """Session loaded from DB survives in-memory cache being cleared."""
        db_file = tmp_path / "asibot.db"
        conn = sqlite3.connect(str(db_file))
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL
            )
            """
        )
        now = time.time()
        conn.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?)",
            ("persist-sess", "carol@example.com", now, now + 3600),
        )
        conn.commit()
        conn.close()

        # Point _db_path to our test DB
        with patch.object(user_session, "_db_path", return_value=str(db_file)):
            # Memory cache is empty -- verify DB fallback works
            ctx = _mock_ctx(session_id="persist-sess")
            uid, err = user_session.require_user(ctx)
            assert uid == "carol@example.com"
            assert err is None

    def test_expired_db_session_not_returned(self, tmp_path):
        """Expired sessions in DB should not be returned."""
        db_file = tmp_path / "asibot.db"
        conn = sqlite3.connect(str(db_file))
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL
            )
            """
        )
        now = time.time()
        conn.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?)",
            ("old-sess", "dave@example.com", now - 7200, now - 3600),
        )
        conn.commit()
        conn.close()

        with (
            patch.object(user_session, "_db_path", return_value=str(db_file)),
            patch.object(user_session.auth, "list_users", return_value=[]),
        ):
            ctx = _mock_ctx(session_id="old-sess")
            uid, err = user_session.require_user(ctx)
            assert uid is None


class TestInvalidationClearsBoth:
    """Invalidation should clear both in-memory and DB sessions."""

    def setup_method(self):
        user_session._session_to_user.clear()
        user_session._auth_failures.clear()

    def teardown_method(self):
        user_session._session_to_user.clear()
        user_session._auth_failures.clear()

    def test_invalidation_clears_memory(self):
        """In-memory sessions are cleared on invalidation."""
        now = time.time()
        user_session._session_to_user["s1"] = ("alice@example.com", now)
        user_session._session_to_user["s2"] = ("bob@example.com", now)
        count = user_session.invalidate_user_sessions("alice@example.com")
        assert count == 1
        assert "s1" not in user_session._session_to_user
        assert "s2" in user_session._session_to_user

    def test_invalidation_schedules_db_delete(self):
        """invalidate_user_sessions should call _schedule_db_delete_user."""
        now = time.time()
        user_session._session_to_user["s1"] = ("alice@example.com", now)
        with patch.object(user_session, "_schedule_db_delete_user") as mock_delete:
            user_session.invalidate_user_sessions("alice@example.com")
            mock_delete.assert_called_once_with("alice@example.com")


class TestLoadSessionsFromDB:
    """Test startup loading of sessions from the database."""

    def setup_method(self):
        user_session._session_to_user.clear()

    def teardown_method(self):
        user_session._session_to_user.clear()

    @pytest.mark.asyncio
    async def test_load_sessions_populates_memory(self):
        """load_sessions_from_db should populate the in-memory cache."""
        mock_sessions = {
            "sess-a": ("alice@example.com", time.time()),
            "sess-b": ("bob@example.com", time.time()),
        }
        with patch("asibot.db.load_active_sessions", return_value=mock_sessions):
            count = await user_session.load_sessions_from_db()
            assert count == 2
            assert "sess-a" in user_session._session_to_user
            assert "sess-b" in user_session._session_to_user
            uid_a, _ = user_session._session_to_user["sess-a"]
            assert uid_a == "alice@example.com"

    @pytest.mark.asyncio
    async def test_load_sessions_handles_failure(self):
        """load_sessions_from_db should return 0 on failure."""
        with patch("asibot.db.load_active_sessions", side_effect=Exception("DB error")):
            count = await user_session.load_sessions_from_db()
            assert count == 0

    @pytest.mark.asyncio
    async def test_load_sessions_respects_max_cap(self):
        """load_sessions_from_db should stop at _MAX_SESSIONS."""
        now = time.time()
        mock_sessions = {f"sess-{i}": (f"user{i}@example.com", now) for i in range(100)}
        with (
            patch.object(user_session, "_MAX_SESSIONS", 10),
            patch("asibot.db.load_active_sessions", return_value=mock_sessions),
        ):
            count = await user_session.load_sessions_from_db()
            assert count == 10
            assert len(user_session._session_to_user) == 10
