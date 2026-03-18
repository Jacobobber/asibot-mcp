"""Tests for server-level validation and setup logic."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from asibot import server


class TestCredentialValidation:
    """Test asibot_set_credentials input validation."""

    @pytest.mark.asyncio
    async def test_rejects_non_dict_json(self):
        ctx = MagicMock()
        with patch.object(server.user_session, "require_user", return_value=("test@example.com", None)):
            # JSON array instead of object
            result = await server.asibot_set_credentials("github", '["token"]', ctx)
            assert "JSON object" in result

    @pytest.mark.asyncio
    async def test_rejects_json_string(self):
        ctx = MagicMock()
        with patch.object(server.user_session, "require_user", return_value=("test@example.com", None)):
            result = await server.asibot_set_credentials("github", '"just a string"', ctx)
            assert "JSON object" in result

    @pytest.mark.asyncio
    async def test_rejects_json_number(self):
        ctx = MagicMock()
        with patch.object(server.user_session, "require_user", return_value=("test@example.com", None)):
            result = await server.asibot_set_credentials("github", '42', ctx)
            assert "JSON object" in result

    @pytest.mark.asyncio
    async def test_rejects_invalid_json(self):
        ctx = MagicMock()
        with patch.object(server.user_session, "require_user", return_value=("test@example.com", None)):
            result = await server.asibot_set_credentials("github", 'not json at all', ctx)
            assert "Invalid JSON" in result

    @pytest.mark.asyncio
    async def test_rejects_missing_fields(self):
        ctx = MagicMock()
        with patch.object(server.user_session, "require_user", return_value=("test@example.com", None)):
            result = await server.asibot_set_credentials("github", '{"token": "ghp_xxx"}', ctx)
            assert "Missing required fields" in result

    @pytest.mark.asyncio
    async def test_rejects_unknown_service(self):
        ctx = MagicMock()
        with patch.object(server.user_session, "require_user", return_value=("test@example.com", None)):
            result = await server.asibot_set_credentials("nonexistent", '{}', ctx)
            assert "Unknown service" in result

    @pytest.mark.asyncio
    async def test_accepts_valid_credentials(self):
        ctx = MagicMock()
        with (
            patch.object(server.user_session, "require_user", return_value=("test@example.com", None)),
            patch.object(server.token_store, "set_credentials"),
        ):
            result = await server.asibot_set_credentials(
                "github", '{"token": "ghp_validtoken123", "org": "myorg"}', ctx
            )
            assert "Connected" in result


class TestSetupCSRF:
    """Verify setup_id is cryptographically random, not derived from device_code."""

    @pytest.mark.asyncio
    async def test_setup_id_is_not_device_code_prefix(self):
        """The setup_id returned to the user must not be a prefix of the device_code."""
        import secrets

        with (
            patch.object(server.user_session, "require_user", return_value=(None, "no user")),
            patch.object(server.settings, "ms365_tenant_id", "test-tenant"),
            patch.object(server.settings, "ms365_client_id", "test-client"),
            patch("asibot.server.httpx.AsyncClient") as mock_http_cls,
            patch("asibot.server.asyncio.create_task") as mock_task,
        ):
            # Mock the device code response
            mock_http = AsyncMock()
            mock_http_cls.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_http.post.return_value = MagicMock(
                json=lambda: {
                    "device_code": "DEVICE_CODE_12345678",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://microsoft.com/devicelogin",
                    "expires_in": 900,
                    "interval": 5,
                },
                raise_for_status=lambda: None,
            )

            ctx = MagicMock()
            result = await server.asibot_setup(ctx)
            # The setup_id in the response must NOT be "DEVICE_C" (device_code[:8])
            assert "DEVICE_C" not in result
            # Must contain a setup_id instruction
            assert "setup_id=" in result


class TestSetupStatus:
    """Test asibot_setup_status with keyed setup IDs."""

    @pytest.mark.asyncio
    async def test_no_setup_in_progress(self):
        server._pending_setups.clear()
        result = await server.asibot_setup_status()
        assert "No setup in progress" in result

    @pytest.mark.asyncio
    async def test_complete_setup_with_id(self):
        server._pending_setups["abc12345"] = {
            "status": "complete",
            "user": {"name": "Test", "user_id": "test@example.com", "api_key": "asb_test"},
        }
        result = await server.asibot_setup_status(setup_id="abc12345")
        assert "Setup complete" in result
        assert "abc12345" not in server._pending_setups

    @pytest.mark.asyncio
    async def test_failed_setup(self):
        server._pending_setups["fail1234"] = {
            "status": "failed",
            "error": "auth_denied",
        }
        result = await server.asibot_setup_status(setup_id="fail1234")
        assert "failed" in result.lower()
        assert "fail1234" not in server._pending_setups

    @pytest.mark.asyncio
    async def test_expired_setup(self):
        server._pending_setups["exp12345"] = {"status": "expired"}
        result = await server.asibot_setup_status(setup_id="exp12345")
        assert "timed out" in result.lower()

    @pytest.mark.asyncio
    async def test_fallback_to_most_recent(self):
        server._pending_setups.clear()
        server._pending_setups["first123"] = {
            "status": "complete",
            "user": {"name": "User1", "user_id": "u1@example.com", "api_key": "asb_1"},
        }
        server._pending_setups["second12"] = {
            "status": "complete",
            "user": {"name": "User2", "user_id": "u2@example.com", "api_key": "asb_2"},
        }
        # Without setup_id, should get the most recent (last inserted)
        result = await server.asibot_setup_status()
        assert "User2" in result
        server._pending_setups.clear()
