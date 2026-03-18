"""Tests for Fernet encryption of credentials at rest."""

import json
import os
import stat
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet

from asibot import crypto
from asibot.config import settings


def _fresh_data_dir(tmp_path: Path):
    """Patch settings.data_dir to use a temp directory and reset Fernet cache."""
    crypto.reset_fernet()
    return patch.object(settings, "data_dir", tmp_path)


class TestKeyManagement:
    def test_generates_key_on_first_use(self, tmp_path):
        with _fresh_data_dir(tmp_path):
            crypto.encrypt_json({"test": True})
            key_file = tmp_path / "master.key"
            assert key_file.exists()
            # Key should be valid Fernet key
            Fernet(key_file.read_bytes().strip())

    def test_key_file_permissions(self, tmp_path):
        with _fresh_data_dir(tmp_path):
            crypto.encrypt_json({"test": True})
            key_file = tmp_path / "master.key"
            mode = os.stat(key_file).st_mode
            # Owner read/write only
            assert mode & stat.S_IRUSR
            assert mode & stat.S_IWUSR
            assert not (mode & stat.S_IRGRP)
            assert not (mode & stat.S_IROTH)

    def test_reuses_existing_key(self, tmp_path):
        with _fresh_data_dir(tmp_path):
            crypto.encrypt_json({"a": 1})
            key1 = (tmp_path / "master.key").read_bytes()
            crypto.reset_fernet()
            crypto.encrypt_json({"b": 2})
            key2 = (tmp_path / "master.key").read_bytes()
            assert key1 == key2


class TestEncryptDecrypt:
    def test_roundtrip(self, tmp_path):
        with _fresh_data_dir(tmp_path):
            data = {"token": "ghp_secret123", "org": "myorg"}
            blob = crypto.encrypt_json(data)
            assert isinstance(blob, bytes)
            assert b"ghp_secret123" not in blob  # actually encrypted
            result = crypto.decrypt_json(blob)
            assert result == data

    def test_encrypted_blob_is_opaque(self, tmp_path):
        with _fresh_data_dir(tmp_path):
            data = {"password": "super_secret"}
            blob = crypto.encrypt_json(data)
            assert b"super_secret" not in blob
            assert b"password" not in blob

    def test_different_encryptions_differ(self, tmp_path):
        with _fresh_data_dir(tmp_path):
            data = {"key": "value"}
            blob1 = crypto.encrypt_json(data)
            blob2 = crypto.encrypt_json(data)
            # Fernet includes random IV, so same plaintext gives different ciphertext
            assert blob1 != blob2


class TestFileOperations:
    def test_save_and_load(self, tmp_path):
        with _fresh_data_dir(tmp_path):
            path = tmp_path / "creds.json"
            data = {"github": {"token": "ghp_xxx"}}
            crypto.save_encrypted(path, data)
            loaded = crypto.load_encrypted(path)
            assert loaded == data

    def test_file_permissions(self, tmp_path):
        with _fresh_data_dir(tmp_path):
            path = tmp_path / "creds.json"
            crypto.save_encrypted(path, {"test": True})
            mode = os.stat(path).st_mode
            assert mode & stat.S_IRUSR
            assert mode & stat.S_IWUSR
            assert not (mode & stat.S_IRGRP)
            assert not (mode & stat.S_IROTH)

    def test_load_nonexistent(self, tmp_path):
        with _fresh_data_dir(tmp_path):
            result = crypto.load_encrypted(tmp_path / "nope.json")
            assert result == {}

    def test_load_corrupted(self, tmp_path):
        with _fresh_data_dir(tmp_path):
            path = tmp_path / "bad.json"
            path.write_bytes(b"not encrypted and not json either \x00\x01")
            result = crypto.load_encrypted(path)
            assert result == {}

    def test_creates_parent_dirs(self, tmp_path):
        with _fresh_data_dir(tmp_path):
            path = tmp_path / "deep" / "nested" / "creds.json"
            crypto.save_encrypted(path, {"a": 1})
            assert crypto.load_encrypted(path) == {"a": 1}


class TestPlaintextMigration:
    def test_migrates_plaintext_json(self, tmp_path):
        with _fresh_data_dir(tmp_path):
            path = tmp_path / "legacy.json"
            data = {"github": {"token": "ghp_old"}, "notion": {"token": "nt_old"}}
            path.write_text(json.dumps(data))

            # First load should detect plaintext and migrate
            loaded = crypto.load_encrypted(path)
            assert loaded == data

            # File should now be encrypted (not readable as plain JSON)
            raw = path.read_bytes()
            try:
                json.loads(raw)
                migrated = False
            except (json.JSONDecodeError, UnicodeDecodeError):
                migrated = True
            assert migrated, "File should be encrypted after migration"

            # Second load should work from encrypted form
            crypto.reset_fernet()  # force re-read of key
            loaded2 = crypto.load_encrypted(path)
            assert loaded2 == data

    def test_migrates_empty_json(self, tmp_path):
        with _fresh_data_dir(tmp_path):
            path = tmp_path / "empty.json"
            path.write_text("{}")
            loaded = crypto.load_encrypted(path)
            assert loaded == {}


class TestKeyRotation:
    def test_rotate_key_reencrypts_files(self, tmp_path):
        with _fresh_data_dir(tmp_path):
            # Set up user data
            users_dir = tmp_path / "users" / "alice"
            users_dir.mkdir(parents=True)
            creds = {"github": {"token": "ghp_secret"}}
            creds_path = users_dir / "credentials.json"
            crypto.save_encrypted(creds_path, creds)

            old_key = (tmp_path / "master.key").read_bytes().strip()

            # Rotate
            result = crypto.rotate_key()
            assert result["status"] == "success"
            assert result["files_migrated"] == 1
            assert result["files_failed"] == 0

            # Old key is backed up
            backup = tmp_path / "master.key.bak"
            assert backup.exists()
            assert backup.read_bytes().strip() == old_key

            # New key is different
            new_key = (tmp_path / "master.key").read_bytes().strip()
            assert new_key != old_key

            # Data is still accessible with new key
            loaded = crypto.load_encrypted(creds_path)
            assert loaded == creds

    def test_rotate_key_no_key_file(self, tmp_path):
        with _fresh_data_dir(tmp_path):
            result = crypto.rotate_key()
            assert "error" in result

    def test_rotate_key_multiple_files(self, tmp_path):
        with _fresh_data_dir(tmp_path):
            users_dir = tmp_path / "users"
            for name in ("alice", "bob", "carol"):
                udir = users_dir / name
                udir.mkdir(parents=True)
                crypto.save_encrypted(udir / "credentials.json", {"service": name})
                crypto.save_encrypted(udir / "preferences.json", {"mode": "read"})

            result = crypto.rotate_key()
            assert result["status"] == "success"
            assert result["files_migrated"] == 6

            # Verify all files still readable
            for name in ("alice", "bob", "carol"):
                udir = users_dir / name
                assert crypto.load_encrypted(udir / "credentials.json")["service"] == name
                assert crypto.load_encrypted(udir / "preferences.json")["mode"] == "read"

    def test_rotate_handles_plaintext_files(self, tmp_path):
        with _fresh_data_dir(tmp_path):
            # Create a key so rotation can proceed
            crypto.encrypt_json({"init": True})

            # Write plaintext JSON (legacy file)
            users_dir = tmp_path / "users" / "legacy"
            users_dir.mkdir(parents=True)
            legacy_path = users_dir / "credentials.json"
            legacy_path.write_text('{"token": "old_value"}')

            result = crypto.rotate_key()
            assert result["status"] == "success"
            assert result["files_migrated"] >= 1

            # Should now be readable with new key
            loaded = crypto.load_encrypted(legacy_path)
            assert loaded["token"] == "old_value"
