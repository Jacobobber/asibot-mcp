"""Tests for role-based access control."""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from asibot.roles import Role, VALID_ROLES, has_permission


# --- Role Hierarchy Tests ---


def test_role_hierarchy():
    assert Role.admin > Role.user > Role.readonly


def test_has_permission_admin():
    assert has_permission("admin", "admin")
    assert has_permission("admin", "user")
    assert has_permission("admin", "readonly")


def test_has_permission_user():
    assert not has_permission("user", "admin")
    assert has_permission("user", "user")
    assert has_permission("user", "readonly")


def test_has_permission_readonly():
    assert not has_permission("readonly", "admin")
    assert not has_permission("readonly", "user")
    assert has_permission("readonly", "readonly")


def test_has_permission_invalid_role():
    assert not has_permission("invalid", "user")
    assert not has_permission("user", "invalid")


def test_valid_roles():
    assert VALID_ROLES == {"admin", "user", "readonly"}


# --- Auth RBAC Tests (async, mocking DB layer) ---


def _make_mock_db():
    """Create a mock db module for patching."""
    mock_db = MagicMock()
    mock_db.get_user_by_email = AsyncMock(return_value=None)
    mock_db.list_users = AsyncMock(return_value=[])
    mock_db.create_user = AsyncMock()
    mock_db.set_role = AsyncMock(return_value=None)
    mock_db.set_role_with_audit = AsyncMock(return_value=None)
    return mock_db


class TestAuthRoles:
    """Test auth module RBAC behaviour.

    auth functions are now async and use lazy imports: `from asibot import db`.
    We patch `asibot.db` to intercept them.
    """

    @pytest.mark.asyncio
    async def test_first_user_default_role(self):
        """When no users exist, the first created user gets 'admin' role."""
        from asibot import auth

        created_user = {"user_id": "a@co.com", "name": "A", "api_key": "asb_x", "role": "admin"}
        mock_db = _make_mock_db()
        mock_db.get_user_by_email = AsyncMock(side_effect=[None, created_user])
        mock_db.list_users = AsyncMock(return_value=[])

        with patch("asibot.db", mock_db):
            user = await auth.create_user("a@co.com", "A")
            assert user["role"] == "admin"

    @pytest.mark.asyncio
    async def test_first_user_explicit_admin(self):
        """Passing role='admin' explicitly creates an admin user."""
        from asibot import auth

        created_user = {"user_id": "a@co.com", "name": "A", "api_key": "asb_x", "role": "admin"}
        mock_db = _make_mock_db()
        mock_db.get_user_by_email = AsyncMock(side_effect=[None, created_user])

        with patch("asibot.db", mock_db):
            user = await auth.create_user("a@co.com", "A", role="admin")
            assert user["role"] == "admin"

    @pytest.mark.asyncio
    async def test_second_user_is_user(self):
        """When users already exist, new user defaults to 'user' role."""
        from asibot import auth

        existing_users = [{"user_id": "a@co.com", "role": "admin"}]
        created_user = {"user_id": "b@co.com", "name": "B", "api_key": "asb_y", "role": "user"}
        mock_db = _make_mock_db()
        mock_db.get_user_by_email = AsyncMock(side_effect=[None, created_user])
        mock_db.list_users = AsyncMock(return_value=existing_users)

        with patch("asibot.db", mock_db):
            user = await auth.create_user("b@co.com", "B")
            assert user["role"] == "user"

    @pytest.mark.asyncio
    async def test_create_user_with_explicit_role(self):
        """Passing an explicit role should be respected."""
        from asibot import auth

        created_user = {"user_id": "r@co.com", "name": "R", "api_key": "asb_z", "role": "readonly"}
        mock_db = _make_mock_db()
        mock_db.get_user_by_email = AsyncMock(side_effect=[None, created_user])

        with patch("asibot.db", mock_db):
            user = await auth.create_user("r@co.com", "R", role="readonly")
            assert user["role"] == "readonly"

    @pytest.mark.asyncio
    async def test_get_role(self):
        """get_role returns the stored role for a known user."""
        from asibot import auth

        mock_db = _make_mock_db()
        mock_db.get_user_by_email = AsyncMock(return_value={"user_id": "a@co.com", "role": "admin"})

        with patch("asibot.db", mock_db):
            assert await auth.get_role("a@co.com") == "admin"

    @pytest.mark.asyncio
    async def test_get_role_missing_user(self):
        """get_role returns 'user' when the email is not found."""
        from asibot import auth

        mock_db = _make_mock_db()
        mock_db.get_user_by_email = AsyncMock(return_value=None)

        with patch("asibot.db", mock_db):
            assert await auth.get_role("nobody@co.com") == "user"

    @pytest.mark.asyncio
    async def test_set_role_invalid(self):
        """set_role raises ValueError for an unrecognised role."""
        from asibot import auth

        mock_audit = MagicMock()
        mock_audit.log_event = MagicMock()

        with patch("asibot.audit", mock_audit):
            with pytest.raises(ValueError, match="Invalid role"):
                await auth.set_role("u@co.com", "superadmin")

    @pytest.mark.asyncio
    async def test_count_admins(self):
        """count_admins counts only users with role='admin'."""
        from asibot import auth

        users = [
            {"role": "admin"},
            {"role": "admin"},
            {"role": "user"},
        ]
        mock_db = _make_mock_db()
        mock_db.list_users = AsyncMock(return_value=users)

        with patch("asibot.db", mock_db):
            assert await auth.count_admins() == 2

    @pytest.mark.asyncio
    async def test_existing_user_returned_same_role(self):
        """create_user returns the existing user unchanged when email exists."""
        from asibot import auth

        existing = {"user_id": "a@co.com", "name": "A", "api_key": "asb_k", "role": "admin"}
        mock_db = _make_mock_db()
        mock_db.get_user_by_email = AsyncMock(return_value=existing)

        with patch("asibot.db", mock_db):
            user = await auth.create_user("a@co.com", "A", role="admin")
            assert user["user_id"] == "a@co.com"
            assert user["role"] == "admin"

    @pytest.mark.asyncio
    async def test_existing_user_role_updated(self):
        """create_user returns the existing user (does not update role on re-create)."""
        from asibot import auth

        existing = {"user_id": "a@co.com", "name": "A", "api_key": "asb_k", "role": "admin"}
        mock_db = _make_mock_db()
        mock_db.get_user_by_email = AsyncMock(return_value=existing)

        with patch("asibot.db", mock_db):
            user = await auth.create_user("a@co.com", "A", role="readonly")
            # create_user returns existing user unchanged
            assert user["user_id"] == "a@co.com"
            assert user["role"] == "admin"


# --- Permission Enforcement Tests (check_permission uses service prefs) ---


class TestCheckPermissionRoles:
    """Test token_store.check_permission().

    check_permission() and user_session.require_user() are async,
    so we use AsyncMock and call them with await.
    """

    @pytest.mark.asyncio
    @patch("asibot.token_store.get_service_prefs", return_value={"enabled": True, "mode": "read"})
    @patch("asibot.token_store.user_session.require_user", new_callable=AsyncMock, return_value=("ro@co.com", None))
    async def test_readonly_blocked_on_write(self, mock_require, mock_prefs):
        from asibot import token_store
        uid, err = await token_store.check_permission(MagicMock(), "github", level="write")
        assert uid is None
        assert "read-only" in err

    @pytest.mark.asyncio
    @patch("asibot.token_store.get_service_prefs", return_value={"enabled": True, "mode": "read"})
    @patch("asibot.token_store.user_session.require_user", new_callable=AsyncMock, return_value=("ro@co.com", None))
    async def test_readonly_allowed_on_read(self, mock_require, mock_prefs):
        from asibot import token_store
        uid, err = await token_store.check_permission(MagicMock(), "github", level="read")
        assert uid == "ro@co.com"
        assert err is None

    @pytest.mark.asyncio
    @patch("asibot.token_store.get_service_prefs", return_value={"enabled": True, "mode": "readwrite"})
    @patch("asibot.token_store.user_session.require_user", new_callable=AsyncMock, return_value=("admin@co.com", None))
    async def test_readwrite_allowed_on_write(self, mock_require, mock_prefs):
        from asibot import token_store
        uid, err = await token_store.check_permission(MagicMock(), "github", level="write")
        assert uid == "admin@co.com"
        assert err is None

    @pytest.mark.asyncio
    @patch("asibot.token_store.get_service_prefs", return_value={"enabled": False, "mode": "read"})
    @patch("asibot.token_store.user_session.require_user", new_callable=AsyncMock, return_value=("user@co.com", None))
    async def test_disabled_service_blocked(self, mock_require, mock_prefs):
        from asibot import token_store
        uid, err = await token_store.check_permission(MagicMock(), "github", level="read")
        assert uid is None
        assert "disabled" in err
