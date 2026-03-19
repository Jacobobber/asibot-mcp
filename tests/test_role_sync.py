"""Tests for role support in user authentication."""

from pathlib import Path
from unittest.mock import patch

from asibot import auth, crypto
from asibot.config import settings


def _fresh_store(tmp_path: Path):
    """Patch auth to use a temp store file with fresh encryption key."""
    crypto.reset_fernet()
    store_path = tmp_path / "users.json"
    return patch.multiple(auth, _users={}, _store_path=store_path)


def _patch_data_dir(tmp_path: Path):
    return patch.object(settings, "data_dir", tmp_path)


def test_get_role_default(tmp_path):
    """User without role field returns 'user'."""
    with _patch_data_dir(tmp_path), _fresh_store(tmp_path):
        user = auth.create_user("nofield@example.com", "NoField")
        # Manually strip the role field to simulate a legacy user
        del user["role"]
        assert auth.get_role("nofield@example.com") == "user"


def test_create_user_with_role(tmp_path):
    """create_user with role='admin' stores and returns admin role."""
    with _patch_data_dir(tmp_path), _fresh_store(tmp_path):
        user = auth.create_user("admin@example.com", "Admin", role="admin")
        assert user["role"] == "admin"
        assert auth.get_role("admin@example.com") == "admin"


def test_set_role(tmp_path):
    """set_role updates an existing user's role."""
    with _patch_data_dir(tmp_path), _fresh_store(tmp_path):
        auth.create_user("changeme@example.com", "ChangeMe")
        assert auth.get_role("changeme@example.com") == "user"

        result = auth.set_role("changeme@example.com", "admin")
        assert result is not None
        assert result["role"] == "admin"
        assert auth.get_role("changeme@example.com") == "admin"


def test_set_role_invalid(tmp_path):
    """set_role with an invalid role returns None."""
    with _patch_data_dir(tmp_path), _fresh_store(tmp_path):
        auth.create_user("valid@example.com", "Valid")
        result = auth.set_role("valid@example.com", "superadmin")
        assert result is None
        # Role should remain unchanged
        assert auth.get_role("valid@example.com") == "user"


def test_count_admins(tmp_path):
    """count_admins returns the correct number of admin users."""
    with _patch_data_dir(tmp_path), _fresh_store(tmp_path):
        auth.create_user("admin1@example.com", "Admin1", role="admin")
        auth.create_user("admin2@example.com", "Admin2", role="admin")
        auth.create_user("regular@example.com", "Regular", role="user")
        assert auth.count_admins() == 2


def test_role_updated_on_re_setup(tmp_path):
    """Calling create_user again with a different role updates it."""
    with _patch_data_dir(tmp_path), _fresh_store(tmp_path):
        user1 = auth.create_user("sync@example.com", "Sync", role="user")
        assert user1["role"] == "user"

        user2 = auth.create_user("sync@example.com", "Sync", role="admin")
        assert user2["role"] == "admin"
        assert auth.get_role("sync@example.com") == "admin"
        # Should be the same user (same API key)
        assert user1["api_key"] == user2["api_key"]
