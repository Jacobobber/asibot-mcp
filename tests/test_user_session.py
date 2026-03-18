"""Tests for user session management, sanitization, and cache eviction."""

import time
from unittest.mock import MagicMock, patch

import pytest

from asibot import user_session
from asibot.config import settings


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
        user_session._session_to_user.clear()
        user_session._auth_failures.clear()

    def teardown_method(self):
        user_session._session_to_user.clear()
        user_session._auth_failures.clear()

    def test_session_cached_with_timestamp(self):
        user_session._session_to_user["sess1"] = ("user@example.com", time.time())
        uid, ts = user_session._session_to_user["sess1"]
        assert uid == "user@example.com"

    def test_evict_stale_sessions(self):
        now = time.time()
        user_session._session_to_user["fresh"] = ("a@b.com", now)
        user_session._session_to_user["stale"] = ("c@d.com", now - 7200)  # 2 hours old
        user_session._evict_stale_sessions()
        assert "fresh" in user_session._session_to_user
        assert "stale" not in user_session._session_to_user

    def test_cache_session_enforces_hard_cap(self):
        """_cache_session should evict LRU entry when at max capacity."""
        now = time.time()
        with patch.object(user_session, "_MAX_SESSIONS", 3):
            user_session._session_to_user["s1"] = ("a@b.com", now - 100)
            user_session._session_to_user["s2"] = ("b@c.com", now - 50)
            user_session._session_to_user["s3"] = ("c@d.com", now - 10)
            # At capacity — adding s4 should evict s1 (LRU / oldest)
            user_session._cache_session("s4", "d@e.com")
            assert "s1" not in user_session._session_to_user
            assert "s4" in user_session._session_to_user
            assert len(user_session._session_to_user) == 3

    def test_lru_eviction_order(self):
        """Accessing a session should move it to end, protecting it from eviction."""
        now = time.time()
        with patch.object(user_session, "_MAX_SESSIONS", 3):
            user_session._session_to_user["s1"] = ("a@b.com", now - 100)
            user_session._session_to_user["s2"] = ("b@c.com", now - 50)
            user_session._session_to_user["s3"] = ("c@d.com", now - 10)
            # Access s1 — moves it to end (most recently used)
            user_session._session_to_user["s1"] = ("a@b.com", now)
            user_session._session_to_user.move_to_end("s1")
            # Now s2 is the LRU — adding s4 should evict s2, not s1
            user_session._cache_session("s4", "d@e.com")
            assert "s1" in user_session._session_to_user  # protected by access
            assert "s2" not in user_session._session_to_user  # evicted as LRU
            assert "s4" in user_session._session_to_user

    def test_invalidate_user_sessions(self):
        """invalidate_user_sessions should remove all sessions for a given user."""
        now = time.time()
        user_session._session_to_user["s1"] = ("alice@example.com", now)
        user_session._session_to_user["s2"] = ("bob@example.com", now)
        user_session._session_to_user["s3"] = ("alice@example.com", now)
        count = user_session.invalidate_user_sessions("alice@example.com")
        assert count == 2
        assert "s1" not in user_session._session_to_user
        assert "s3" not in user_session._session_to_user
        assert "s2" in user_session._session_to_user  # bob unaffected

    def test_invalidate_no_sessions(self):
        """invalidate_user_sessions on non-existent user returns 0."""
        count = user_session.invalidate_user_sessions("nobody@example.com")
        assert count == 0

    def test_expired_session_not_returned(self):
        # Pre-populate with an expired session
        user_session._session_to_user["expired-sess"] = ("old@example.com", time.time() - 7200)

        ctx = MagicMock()
        headers = MagicMock()
        headers.get = MagicMock(side_effect=lambda k, d="": {"mcp-session-id": "expired-sess"}.get(k, d))
        ctx.request_context.request.headers = headers

        with patch.object(user_session.auth, "list_users", return_value=[]):
            uid, err = user_session.require_user(ctx)
            assert uid is None
            assert "No users" in err


class TestRateLimiting:
    def setup_method(self):
        user_session._auth_failures.clear()

    def teardown_method(self):
        user_session._auth_failures.clear()

    def test_not_rate_limited_initially(self):
        assert not user_session._is_rate_limited("test_pfx")

    def test_rate_limited_after_max_failures(self):
        now = time.time()
        user_session._auth_failures["test_pfx"] = [now] * user_session._AUTH_FAIL_MAX
        assert user_session._is_rate_limited("test_pfx")

    def test_old_failures_pruned(self):
        old = time.time() - user_session._AUTH_FAIL_WINDOW - 1
        user_session._auth_failures["test_pfx"] = [old] * user_session._AUTH_FAIL_MAX
        # Old failures should not count
        assert not user_session._is_rate_limited("test_pfx")

    def test_invalid_api_key_records_failure(self):
        ctx = _mock_ctx(api_key="bad_key_123")
        with (
            patch.object(user_session.auth, "get_user_by_key", return_value=None),
            patch.object(user_session.auth, "list_users", return_value=[]),
        ):
            uid, err = user_session.require_user(ctx)
            assert uid is None
            assert "Invalid API key" in err
            key_pfx = user_session._key_prefix("bad_key_123")
            assert len(user_session._auth_failures.get(key_pfx, [])) == 1

    def test_rate_limited_response(self):
        # Fill up the failure list for a specific key prefix
        now = time.time()
        key_pfx = user_session._key_prefix("any_key_456")
        user_session._auth_failures[key_pfx] = [now] * user_session._AUTH_FAIL_MAX

        ctx = _mock_ctx(api_key="any_key_456")
        uid, err = user_session.require_user(ctx)
        assert uid is None
        assert "Too many failed" in err

    def test_different_keys_independent(self):
        """Rate limiting for one key should not affect another."""
        now = time.time()
        pfx_a = user_session._key_prefix("key_aaaa")
        pfx_b = user_session._key_prefix("key_bbbb")
        user_session._auth_failures[pfx_a] = [now] * user_session._AUTH_FAIL_MAX

        assert user_session._is_rate_limited(pfx_a)
        assert not user_session._is_rate_limited(pfx_b)


class TestSingleUserAutoLogin:
    def setup_method(self):
        user_session._session_to_user.clear()
        user_session._auth_failures.clear()

    def teardown_method(self):
        user_session._session_to_user.clear()
        user_session._auth_failures.clear()

    def test_auto_login_allowed_on_stdio(self):
        """Single-user auto-login should work on stdio transport."""
        ctx = _mock_ctx()  # no API key
        mock_user = {"user_id": "sole@example.com", "name": "Solo", "api_key": "key"}
        with (
            patch.object(settings, "transport", "stdio"),
            patch.object(user_session.auth, "list_users", return_value=[mock_user]),
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
        ):
            uid, err = user_session.require_user(ctx)
            assert uid is None
            assert "Authentication required" in err


class TestRequireUser:
    def setup_method(self):
        user_session._session_to_user.clear()
        user_session._auth_failures.clear()

    def teardown_method(self):
        user_session._session_to_user.clear()
        user_session._auth_failures.clear()

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
            # Verify it was cached
            assert "sess-abc" in user_session._session_to_user
            cached_uid, _ = user_session._session_to_user["sess-abc"]
            assert cached_uid == "test@example.com"
