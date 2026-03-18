"""Tests for production readiness fixes: global rate limiting, credential validation, token refresh."""

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from asibot import crypto, token_store, user_session, validation
from asibot.config import settings


# --- Helpers ---


def _setup_user_dir(tmp_path: Path, user_id: str = "testuser@example.com") -> Path:
    user_dir = tmp_path / "users" / user_id.replace("@", "_at_")
    user_dir.mkdir(parents=True)
    return user_dir


def _patch_crypto(tmp_path: Path):
    crypto.reset_fernet()
    return patch.object(settings, "data_dir", tmp_path)


# =============================================================================
# Issue 9: Global Per-Service Rate Limiting
# =============================================================================


class TestGlobalRateLimiter:
    def setup_method(self):
        token_store.global_rate_limiter.reset()

    def test_allows_requests_under_limit(self):
        for _ in range(5):
            allowed, _ = token_store.global_rate_limiter.check("github")
            assert allowed

    def test_blocks_at_limit(self):
        with patch.object(settings, "global_rate_limits", {"testservice": 3}):
            for i in range(3):
                allowed, _ = token_store.global_rate_limiter.check("testservice")
                assert allowed, f"Request {i+1} should be allowed"
            # 4th request should be blocked
            allowed, retry_after = token_store.global_rate_limiter.check("testservice")
            assert not allowed
            assert retry_after > 0

    def test_uses_default_limit(self):
        with patch.object(settings, "global_rate_limit_default", 5):
            for _ in range(5):
                allowed, _ = token_store.global_rate_limiter.check("unknown_service")
                assert allowed
            allowed, _ = token_store.global_rate_limiter.check("unknown_service")
            assert not allowed

    def test_per_service_override(self):
        with (
            patch.object(settings, "global_rate_limit_default", 100),
            patch.object(settings, "global_rate_limits", {"limited_svc": 2}),
        ):
            # limited_svc has limit=2
            token_store.global_rate_limiter.check("limited_svc")
            token_store.global_rate_limiter.check("limited_svc")
            allowed, _ = token_store.global_rate_limiter.check("limited_svc")
            assert not allowed

            # other_svc uses default=100, should still work
            allowed, _ = token_store.global_rate_limiter.check("other_svc")
            assert allowed

    def test_hit_counter_increments(self):
        with patch.object(settings, "global_rate_limits", {"svc": 1}):
            token_store.global_rate_limiter.check("svc")  # allowed
            assert token_store.global_rate_limiter.get_hits("svc") == 0
            token_store.global_rate_limiter.check("svc")  # blocked
            assert token_store.global_rate_limiter.get_hits("svc") == 1
            token_store.global_rate_limiter.check("svc")  # blocked again
            assert token_store.global_rate_limiter.get_hits("svc") == 2

    def test_reset_clears_state(self):
        token_store.global_rate_limiter.check("svc")
        token_store.global_rate_limiter.reset()
        assert token_store.global_rate_limiter.get_hits("svc") == 0

    def test_different_services_independent(self):
        with patch.object(settings, "global_rate_limits", {"a": 1, "b": 1}):
            allowed_a, _ = token_store.global_rate_limiter.check("a")
            assert allowed_a
            # a is now exhausted
            blocked_a, _ = token_store.global_rate_limiter.check("a")
            assert not blocked_a
            # b should still be available
            allowed_b, _ = token_store.global_rate_limiter.check("b")
            assert allowed_b


class TestSafeRequestRateLimit:
    """Test that safe_request checks global rate limit."""

    def setup_method(self):
        token_store.global_rate_limiter.reset()

    @pytest.mark.asyncio
    async def test_safe_request_blocked_by_global_limit(self):
        with patch.object(settings, "global_rate_limits", {"github": 1}):
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_client.request = AsyncMock(return_value=mock_response)

            # First request succeeds
            resp, err = await token_store.safe_request(
                mock_client, "GET", "/test", service="github", action="test"
            )
            assert err is None

            # Second request blocked by rate limit
            resp, err = await token_store.safe_request(
                mock_client, "GET", "/test", service="github", action="test"
            )
            assert resp is None
            assert "Service rate limit reached" in err
            assert "retry after" in err

    @pytest.mark.asyncio
    async def test_safe_request_normalizes_service_name(self):
        """Service names with spaces/caps are normalized for rate limiting."""
        with patch.object(settings, "global_rate_limits", {"google_drive": 1}):
            mock_client = AsyncMock()
            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_client.request = AsyncMock(return_value=mock_response)

            # "Google Drive" -> "google_drive" for rate limit key
            resp, err = await token_store.safe_request(
                mock_client, "GET", "/test", service="Google Drive", action="search"
            )
            assert err is None

            resp, err = await token_store.safe_request(
                mock_client, "GET", "/test", service="Google Drive", action="search"
            )
            assert "rate limit" in err.lower()


# =============================================================================
# Issue 10: Credential Validation Before Storage
# =============================================================================


class TestStripCredentialValues:
    def test_strips_whitespace(self):
        result = validation.strip_credential_values({"token": "  ghp_xxx  ", "org": " myorg "})
        assert result["token"] == "ghp_xxx"
        assert result["org"] == "myorg"

    def test_handles_non_string_values(self):
        result = validation.strip_credential_values({"token": "abc", "count": 42})
        assert result["token"] == "abc"
        assert result["count"] == 42

    def test_empty_string_stays_empty(self):
        result = validation.strip_credential_values({"token": "   "})
        assert result["token"] == ""


class TestValidateCredentials:
    def test_accepts_valid_github_token(self):
        assert validation.validate_credentials("github", {"token": "ghp_abcdefghij1234"}) is None

    def test_accepts_ghs_token(self):
        assert validation.validate_credentials("github", {"token": "ghs_abcdefghij1234"}) is None

    def test_accepts_github_pat_token(self):
        assert validation.validate_credentials("github", {"token": "github_pat_abcdefghij"}) is None

    def test_accepts_gho_token(self):
        assert validation.validate_credentials("github", {"token": "gho_abcdefghij1234"}) is None

    def test_rejects_wrong_github_prefix(self):
        result = validation.validate_credentials("github", {"token": "sk-abcdefghij12345"})
        assert result is not None
        assert "ghp_" in result
        assert "sk-a..." in result

    def test_rejects_empty_field(self):
        result = validation.validate_credentials("github", {"token": ""})
        assert result is not None
        assert "empty" in result.lower()

    def test_salesforce_valid(self):
        assert validation.validate_credentials(
            "salesforce", {"token": "sf_longtoken12345", "instance_url": "https://myco.salesforce.com"}
        ) is None

    def test_salesforce_rejects_http_url(self):
        result = validation.validate_credentials(
            "salesforce", {"token": "sf_longtoken12345", "instance_url": "http://myco.salesforce.com"}
        )
        assert result is not None
        assert "https://" in result.lower()

    def test_sap_rejects_http_base_url(self):
        result = validation.validate_credentials(
            "sap", {"token": "sap_longtoken12345", "base_url": "http://sap.example.com"}
        )
        assert result is not None
        assert "https://" in result.lower()

    def test_rejects_short_token(self):
        result = validation.validate_credentials("notion", {"token": "short"})
        assert result is not None
        assert "too short" in result.lower()

    def test_accepts_long_enough_token(self):
        assert validation.validate_credentials("notion", {"token": "a" * 10}) is None

    def test_accepts_unknown_service_with_valid_creds(self):
        # Unknown services should pass basic validation (non-empty, min length)
        assert validation.validate_credentials("custom_svc", {"token": "abcdefghij"}) is None

    def test_accepts_zoom_credentials(self):
        # Zoom uses client_id/secret/account_id — no token prefix check
        assert validation.validate_credentials(
            "zoom", {"account_id": "abcdefghij", "client_id": "abcdefghij", "client_secret": "abcdefghij1234"}
        ) is None

    def test_rejects_api_key_too_short(self):
        result = validation.validate_credentials("zapier", {"api_key": "short"})
        assert result is not None
        assert "too short" in result.lower()


class TestSetCredentialsValidation:
    """Integration test: validation wired into asibot_set_credentials."""

    @pytest.mark.asyncio
    async def test_strips_and_validates(self):
        from asibot import server

        ctx = MagicMock()
        with (
            patch.object(server.user_session, "require_user", return_value=("test@example.com", None)),
            patch.object(server.token_store, "set_credentials"),
            patch.object(settings, "github_org", "myorg"),
        ):
            # Whitespace around token should be stripped, valid prefix accepted
            result = await server.asibot_set_credentials(
                "github", '{"token": "  ghp_validtoken123  "}', ctx
            )
            assert "Connected" in result

    @pytest.mark.asyncio
    async def test_rejects_invalid_github_prefix(self):
        from asibot import server

        ctx = MagicMock()
        with (
            patch.object(server.user_session, "require_user", return_value=("test@example.com", None)),
            patch.object(settings, "github_org", "myorg"),
        ):
            result = await server.asibot_set_credentials(
                "github", '{"token": "sk-wrong_prefix_12345"}', ctx
            )
            assert "ghp_" in result  # shows expected prefixes

    @pytest.mark.asyncio
    async def test_rejects_empty_after_strip(self):
        from asibot import server

        ctx = MagicMock()
        with (
            patch.object(server.user_session, "require_user", return_value=("test@example.com", None)),
            patch.object(settings, "github_org", "myorg"),
        ):
            result = await server.asibot_set_credentials(
                "github", '{"token": "   "}', ctx
            )
            # After stripping, token is empty -> missing required field
            assert "Missing required fields" in result


# =============================================================================
# Issue 11: Token Refresh for OAuth Providers
# =============================================================================


class TestIsTokenExpired:
    def test_not_expired(self):
        creds = {"token": "abc", "expires_at": str(time.time() + 3600)}
        assert not token_store.is_token_expired(creds)

    def test_expired(self):
        creds = {"token": "abc", "expires_at": str(time.time() - 100)}
        assert token_store.is_token_expired(creds)

    def test_expires_within_margin(self):
        # Expires in 60 seconds but margin is 300 -> considered expired
        creds = {"token": "abc", "expires_at": str(time.time() + 60)}
        assert token_store.is_token_expired(creds, margin_seconds=300)

    def test_no_expires_at_not_expired(self):
        creds = {"token": "abc"}
        assert not token_store.is_token_expired(creds)

    def test_invalid_expires_at(self):
        creds = {"token": "abc", "expires_at": "not_a_number"}
        assert not token_store.is_token_expired(creds)

    def test_custom_margin(self):
        creds = {"token": "abc", "expires_at": str(time.time() + 10)}
        assert not token_store.is_token_expired(creds, margin_seconds=5)
        assert token_store.is_token_expired(creds, margin_seconds=15)


class TestRefreshOAuthToken:
    @pytest.mark.asyncio
    async def test_successful_refresh(self, tmp_path):
        user_dir = _setup_user_dir(tmp_path)
        with (
            _patch_crypto(tmp_path),
            patch.object(user_session, "get_user_data_dir", return_value=user_dir),
        ):
            # Store initial credentials
            token_store.set_credentials("testuser@example.com", "google", {
                "token": "old_token",
                "refresh_token": "rt_abc",
                "expires_at": str(time.time() - 100),
            })

            # Mock the HTTP refresh call
            mock_response = MagicMock()
            mock_response.json.return_value = {
                "access_token": "new_token_12345",
                "refresh_token": "new_rt_abc",
                "expires_in": 3600,
            }
            mock_response.raise_for_status = MagicMock()

            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=mock_response)
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)

            with patch("asibot.token_store.httpx.AsyncClient", return_value=mock_http):
                result = await token_store.refresh_oauth_token(
                    service="google",
                    user_id="testuser@example.com",
                    refresh_url="https://oauth2.googleapis.com/token",
                    client_id="test_client_id",
                    client_secret="test_client_secret",
                    refresh_token="rt_abc",
                )

            assert result is not None
            assert result["token"] == "new_token_12345"
            assert result["refresh_token"] == "new_rt_abc"

            # Verify stored credentials updated
            stored = token_store.get_credentials("testuser@example.com", "google")
            assert stored["token"] == "new_token_12345"

    @pytest.mark.asyncio
    async def test_refresh_failure_returns_none(self, tmp_path):
        user_dir = _setup_user_dir(tmp_path)
        import httpx

        with (
            _patch_crypto(tmp_path),
            patch.object(user_session, "get_user_data_dir", return_value=user_dir),
        ):
            token_store.set_credentials("testuser@example.com", "google", {
                "token": "old_token",
                "refresh_token": "rt_abc",
            })

            mock_http = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                "401", request=MagicMock(), response=MagicMock(status_code=401)
            )
            mock_http.post = AsyncMock(return_value=mock_resp)
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)

            with patch("asibot.token_store.httpx.AsyncClient", return_value=mock_http):
                result = await token_store.refresh_oauth_token(
                    service="google",
                    user_id="testuser@example.com",
                    refresh_url="https://oauth2.googleapis.com/token",
                    client_id="test_client_id",
                    client_secret="test_client_secret",
                    refresh_token="rt_abc",
                )

            assert result is None


class TestGoogleTokenRefresh:
    """Test the Google Workspace connector's token refresh integration."""

    @pytest.mark.asyncio
    async def test_ensure_google_token_not_expired(self, tmp_path):
        from asibot.connectors.google_workspace import _ensure_google_token

        user_dir = _setup_user_dir(tmp_path)
        with (
            _patch_crypto(tmp_path),
            patch.object(user_session, "get_user_data_dir", return_value=user_dir),
        ):
            # Token not expired — should return None (no error)
            token_store.set_credentials("testuser@example.com", "google", {
                "token": "valid_token",
                "expires_at": str(time.time() + 3600),
            })
            result = await _ensure_google_token("testuser@example.com")
            assert result is None

    @pytest.mark.asyncio
    async def test_ensure_google_token_expired_no_refresh(self, tmp_path):
        from asibot.connectors.google_workspace import _ensure_google_token

        user_dir = _setup_user_dir(tmp_path)
        with (
            _patch_crypto(tmp_path),
            patch.object(user_session, "get_user_data_dir", return_value=user_dir),
        ):
            # Token expired, no refresh token
            token_store.set_credentials("testuser@example.com", "google", {
                "token": "expired_token",
                "expires_at": str(time.time() - 100),
            })
            result = await _ensure_google_token("testuser@example.com")
            assert result is not None
            assert "reconnect" in result.lower()

    @pytest.mark.asyncio
    async def test_ensure_google_token_refreshes_successfully(self, tmp_path):
        from asibot.connectors.google_workspace import _ensure_google_token

        user_dir = _setup_user_dir(tmp_path)
        with (
            _patch_crypto(tmp_path),
            patch.object(user_session, "get_user_data_dir", return_value=user_dir),
            patch.object(settings, "google_client_id", "test_id"),
            patch.object(settings, "google_client_secret", "test_secret"),
        ):
            token_store.set_credentials("testuser@example.com", "google", {
                "token": "expired_token",
                "refresh_token": "rt_valid",
                "expires_at": str(time.time() - 100),
            })

            mock_response = MagicMock()
            mock_response.json.return_value = {
                "access_token": "fresh_token",
                "refresh_token": "rt_new",
                "expires_in": 3600,
            }
            mock_response.raise_for_status = MagicMock()

            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=mock_response)
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)

            with patch("asibot.token_store.httpx.AsyncClient", return_value=mock_http):
                result = await _ensure_google_token("testuser@example.com")

            assert result is None  # no error

            # Verify stored creds were updated
            creds = token_store.get_credentials("testuser@example.com", "google")
            assert creds["token"] == "fresh_token"

    @pytest.mark.asyncio
    async def test_ensure_google_token_no_creds(self, tmp_path):
        from asibot.connectors.google_workspace import _ensure_google_token

        user_dir = _setup_user_dir(tmp_path)
        with (
            _patch_crypto(tmp_path),
            patch.object(user_session, "get_user_data_dir", return_value=user_dir),
        ):
            # No credentials stored — should return None (require_service handles this)
            result = await _ensure_google_token("testuser@example.com")
            assert result is None


class TestZoomPaylocityTokenRefresh:
    """Verify Zoom and Paylocity already handle token refresh via client credentials."""

    def test_zoom_token_cache_has_margin(self):
        from asibot.connectors.zoom import _TOKEN_MARGIN
        assert _TOKEN_MARGIN == 300  # 5 minutes proactive refresh

    def test_paylocity_token_cache_has_margin(self):
        from asibot.connectors.paylocity import _TOKEN_MARGIN
        assert _TOKEN_MARGIN == 300  # 5 minutes proactive refresh
