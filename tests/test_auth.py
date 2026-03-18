"""Tests for user authentication and API key store."""

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


def test_create_user(tmp_path):
    with _patch_data_dir(tmp_path), _fresh_store(tmp_path):
        user = auth.create_user("alice@example.com", "Alice")
        assert user["user_id"] == "alice@example.com"
        assert user["name"] == "Alice"
        assert user["api_key"].startswith("asb_")
        assert "created_at" in user


def test_create_user_idempotent(tmp_path):
    with _patch_data_dir(tmp_path), _fresh_store(tmp_path):
        u1 = auth.create_user("bob@example.com", "Bob")
        u2 = auth.create_user("bob@example.com", "Bob")
        assert u1["api_key"] == u2["api_key"]


def test_get_user_by_key(tmp_path):
    with _patch_data_dir(tmp_path), _fresh_store(tmp_path):
        user = auth.create_user("carol@example.com", "Carol")
        found = auth.get_user_by_key(user["api_key"])
        assert found is not None
        assert found["user_id"] == "carol@example.com"


def test_get_user_by_key_not_found(tmp_path):
    with _patch_data_dir(tmp_path), _fresh_store(tmp_path):
        assert auth.get_user_by_key("nonexistent_key") is None


def test_get_user_by_email(tmp_path):
    with _patch_data_dir(tmp_path), _fresh_store(tmp_path):
        auth.create_user("dave@example.com", "Dave")
        found = auth.get_user_by_email("dave@example.com")
        assert found is not None
        assert found["name"] == "Dave"


def test_get_user_by_email_not_found(tmp_path):
    with _patch_data_dir(tmp_path), _fresh_store(tmp_path):
        assert auth.get_user_by_email("nobody@example.com") is None


def test_list_users(tmp_path):
    with _patch_data_dir(tmp_path), _fresh_store(tmp_path):
        assert auth.list_users() == []
        auth.create_user("eve@example.com", "Eve")
        auth.create_user("frank@example.com", "Frank")
        users = auth.list_users()
        assert len(users) == 2
        emails = {u["user_id"] for u in users}
        assert emails == {"eve@example.com", "frank@example.com"}


def test_load_corrupted_store(tmp_path):
    with _patch_data_dir(tmp_path), _fresh_store(tmp_path):
        store_path = tmp_path / "users.json"
        store_path.write_bytes(b"\x00\x01\x02 garbage data")
        # Should not raise — returns empty
        assert auth.get_user_by_key("anything") is None


def test_persistence(tmp_path):
    store_path = tmp_path / "users.json"
    crypto.reset_fernet()
    with _patch_data_dir(tmp_path), patch.multiple(auth, _users={}, _store_path=store_path):
        user = auth.create_user("persist@example.com", "Persist")

    # Simulate fresh load from disk — reset Fernet so it re-reads the same key from disk
    crypto.reset_fernet()
    with _patch_data_dir(tmp_path), patch.multiple(auth, _users={}, _store_path=store_path):
        found = auth.get_user_by_email("persist@example.com")
        assert found is not None
        assert found["api_key"] == user["api_key"]


def test_users_file_is_encrypted(tmp_path):
    """Verify the users.json file on disk is not readable as plain JSON."""
    store_path = tmp_path / "users.json"
    with _patch_data_dir(tmp_path), patch.multiple(auth, _users={}, _store_path=store_path):
        auth.create_user("secret@example.com", "Secret")

    raw = store_path.read_bytes()
    assert b"secret@example.com" not in raw
    assert b"asb_" not in raw


def test_rotate_key(tmp_path):
    with _patch_data_dir(tmp_path), _fresh_store(tmp_path):
        user = auth.create_user("rotate@example.com", "Rotate")
        old_key = user["api_key"]
        rotated = auth.rotate_key("rotate@example.com")
        assert rotated is not None
        assert rotated["api_key"] != old_key
        assert rotated["api_key"].startswith("asb_")
        assert "key_rotated_at" in rotated
        # Old key should no longer work
        assert auth.get_user_by_key(old_key) is None
        # New key should work
        assert auth.get_user_by_key(rotated["api_key"]) is not None


def test_rotate_key_not_found(tmp_path):
    with _patch_data_dir(tmp_path), _fresh_store(tmp_path):
        assert auth.rotate_key("nobody@example.com") is None
