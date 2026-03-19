"""Tests for role-based access control."""

import pytest
from unittest.mock import patch, MagicMock

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


# --- Auth RBAC Tests (sync, mocking file-based _load/_save) ---


class TestAuthRoles:
    """Test auth module RBAC behaviour.

    auth.create_user() is synchronous and uses file-backed _load/_save
    (encrypted JSON), so we mock those helpers rather than a DB layer.
    """

    def test_first_user_default_role(self):
        """When no users exist, the first created user gets the default 'user' role."""
        from asibot import auth

        # _load populates auth._users; we simulate an empty store
        with patch.object(auth, "_load"), \
             patch.object(auth, "_save"), \
             patch.object(auth, "_users", {}):
            user = auth.create_user("a@co.com", "A")
            assert user["role"] == "user"

    def test_first_user_explicit_admin(self):
        """Passing role='admin' explicitly creates an admin user."""
        from asibot import auth

        with patch.object(auth, "_load"), \
             patch.object(auth, "_save"), \
             patch.object(auth, "_users", {}):
            user = auth.create_user("a@co.com", "A", role="admin")
            assert user["role"] == "admin"

    def test_second_user_is_user(self):
        """When users already exist, new user defaults to 'user' role."""
        from asibot import auth

        existing_users = {
            "asb_existing": {
                "user_id": "a@co.com",
                "name": "A",
                "api_key": "asb_existing",
                "role": "admin",
            }
        }
        with patch.object(auth, "_load"), \
             patch.object(auth, "_save"), \
             patch.object(auth, "_users", dict(existing_users)):
            user = auth.create_user("b@co.com", "B")
            assert user["role"] == "user"

    def test_create_user_with_explicit_role(self):
        """Passing an explicit role should be respected."""
        from asibot import auth

        with patch.object(auth, "_load"), \
             patch.object(auth, "_save"), \
             patch.object(auth, "_users", {}):
            user = auth.create_user("r@co.com", "R", role="readonly")
            assert user["role"] == "readonly"

    def test_get_role(self):
        """get_role returns the stored role for a known user."""
        from asibot import auth

        users = {
            "k1": {"user_id": "a@co.com", "role": "admin"},
        }
        with patch.object(auth, "_load"), \
             patch.object(auth, "_users", users):
            assert auth.get_role("a@co.com") == "admin"

    def test_get_role_missing_user(self):
        """get_role returns 'user' when the email is not found."""
        from asibot import auth

        with patch.object(auth, "_load"), \
             patch.object(auth, "_users", {}):
            assert auth.get_role("nobody@co.com") == "user"

    def test_set_role_invalid(self):
        """set_role returns None for an unrecognised role."""
        from asibot import auth

        result = auth.set_role("u@co.com", "superadmin")
        assert result is None

    def test_count_admins(self):
        """count_admins counts only users with role='admin'."""
        from asibot import auth

        users = {
            "k1": {"role": "admin"},
            "k2": {"role": "admin"},
            "k3": {"role": "user"},
        }
        with patch.object(auth, "_load"), \
             patch.object(auth, "_users", users):
            assert auth.count_admins() == 2

    def test_existing_user_returned_same_role(self):
        """create_user returns the existing user unchanged when role matches."""
        from asibot import auth

        existing = {
            "asb_k": {
                "user_id": "a@co.com",
                "name": "A",
                "api_key": "asb_k",
                "role": "admin",
            }
        }
        with patch.object(auth, "_load"), \
             patch.object(auth, "_save"), \
             patch.object(auth, "_users", dict(existing)):
            user = auth.create_user("a@co.com", "A", role="admin")
            assert user["user_id"] == "a@co.com"
            assert user["role"] == "admin"

    def test_existing_user_role_updated(self):
        """create_user updates role when it differs from the existing one."""
        from asibot import auth

        existing = {
            "asb_k": {
                "user_id": "a@co.com",
                "name": "A",
                "api_key": "asb_k",
                "role": "admin",
            }
        }
        with patch.object(auth, "_load"), \
             patch.object(auth, "_save") as mock_save, \
             patch.object(auth, "_users", dict(existing)):
            user = auth.create_user("a@co.com", "A", role="readonly")
            assert user["user_id"] == "a@co.com"
            assert user["role"] == "readonly"
            mock_save.assert_called_once()


# --- Permission Enforcement Tests (check_permission uses service prefs) ---


class TestCheckPermissionRoles:
    """Test token_store.check_permission().

    check_permission() and user_session.require_user() are both synchronous,
    so we use MagicMock (not AsyncMock) and call them without await.
    """

    @patch("asibot.token_store.get_service_prefs", return_value={"enabled": True, "mode": "read"})
    @patch("asibot.token_store.user_session.require_user", return_value=("ro@co.com", None))
    def test_readonly_blocked_on_write(self, mock_require, mock_prefs):
        from asibot import token_store
        uid, err = token_store.check_permission(MagicMock(), "github", level="write")
        assert uid is None
        assert "read-only" in err

    @patch("asibot.token_store.get_service_prefs", return_value={"enabled": True, "mode": "read"})
    @patch("asibot.token_store.user_session.require_user", return_value=("ro@co.com", None))
    def test_readonly_allowed_on_read(self, mock_require, mock_prefs):
        from asibot import token_store
        uid, err = token_store.check_permission(MagicMock(), "github", level="read")
        assert uid == "ro@co.com"
        assert err is None

    @patch("asibot.token_store.get_service_prefs", return_value={"enabled": True, "mode": "readwrite"})
    @patch("asibot.token_store.user_session.require_user", return_value=("admin@co.com", None))
    def test_readwrite_allowed_on_write(self, mock_require, mock_prefs):
        from asibot import token_store
        uid, err = token_store.check_permission(MagicMock(), "github", level="write")
        assert uid == "admin@co.com"
        assert err is None

    @patch("asibot.token_store.get_service_prefs", return_value={"enabled": False, "mode": "read"})
    @patch("asibot.token_store.user_session.require_user", return_value=("user@co.com", None))
    def test_disabled_service_blocked(self, mock_require, mock_prefs):
        from asibot import token_store
        uid, err = token_store.check_permission(MagicMock(), "github", level="read")
        assert uid is None
        assert "disabled" in err
