"""Fernet encryption for credential storage.

Master key is generated once and stored at ~/.asibot/master.key with
restricted file permissions (owner-only read/write). All credential and
token files are encrypted at rest using this key.

Transparently migrates existing plaintext JSON files on first read.
"""

import json
import logging
import os
import stat
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from asibot.config import settings

logger = logging.getLogger(__name__)

_fernet: Fernet | None = None


def _key_path() -> Path:
    return settings.data_dir / "master.key"


def _get_fernet() -> Fernet:
    """Get or create the Fernet instance, generating the master key if needed."""
    global _fernet
    if _fernet is not None:
        return _fernet

    key_file = _key_path()
    if key_file.exists():
        key = key_file.read_bytes().strip()
    else:
        key = Fernet.generate_key()
        settings.ensure_dirs()
        key_file.write_bytes(key)
        # Restrict to owner read/write only
        os.chmod(key_file, stat.S_IRUSR | stat.S_IWUSR)
        logger.info("Generated new master key at %s", key_file)

    _fernet = Fernet(key)
    return _fernet


def encrypt_json(data: dict) -> bytes:
    """Serialize dict to JSON and encrypt it."""
    f = _get_fernet()
    return f.encrypt(json.dumps(data).encode("utf-8"))


def decrypt_json(blob: bytes) -> dict:
    """Decrypt bytes and deserialize as JSON dict."""
    f = _get_fernet()
    return json.loads(f.decrypt(blob).decode("utf-8"))


def save_encrypted(path: Path, data: dict) -> None:
    """Encrypt a dict and write to file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encrypt_json(data))
    # Restrict credential files to owner only
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def load_encrypted(path: Path) -> dict:
    """Load and decrypt a file. Handles migration from plaintext JSON.

    If the file contains valid plaintext JSON, it is transparently
    re-encrypted in place. Returns empty dict if file doesn't exist.
    """
    if not path.exists():
        return {}

    raw = path.read_bytes()

    # Try decrypting first (normal path)
    try:
        return decrypt_json(raw)
    except InvalidToken:
        pass

    # Might be a legacy plaintext JSON file — try migrating
    try:
        data = json.loads(raw.decode("utf-8"))
        logger.info("Migrating plaintext file to encrypted: %s", path)
        save_encrypted(path, data)
        return data
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        logger.warning("Failed to load %s: %s", path, e)
        return {}


def rotate_key() -> dict:
    """Generate a new master key and re-encrypt all user data files.

    The old key is backed up as master.key.bak before the new key is written.
    Returns a summary dict with counts of migrated/failed files.
    """
    key_file = _key_path()
    if not key_file.exists():
        return {"error": "No master key found. Nothing to rotate."}

    old_key = key_file.read_bytes().strip()
    old_fernet = Fernet(old_key)
    new_key = Fernet.generate_key()
    new_fernet = Fernet(new_key)

    # Find all encrypted user data files
    users_dir = settings.data_dir / "users"
    encrypted_files: list[Path] = []
    if users_dir.exists():
        for f in users_dir.rglob("*.json"):
            encrypted_files.append(f)

    # Re-encrypt all files with the new key (in memory first)
    re_encrypted: list[tuple[Path, bytes]] = []
    failed: list[str] = []
    for path in encrypted_files:
        raw = path.read_bytes()
        try:
            plaintext = old_fernet.decrypt(raw)
            re_encrypted.append((path, new_fernet.encrypt(plaintext)))
        except InvalidToken:
            # Might be plaintext or corrupted — try to load as JSON
            try:
                json.loads(raw)
                # It's valid plaintext JSON — encrypt with new key
                re_encrypted.append((path, new_fernet.encrypt(raw)))
            except (json.JSONDecodeError, UnicodeDecodeError):
                failed.append(str(path))
                logger.warning("Key rotation: could not decrypt %s, skipping", path)

    # Backup old key
    backup_path = key_file.with_suffix(".key.bak")
    backup_path.write_bytes(old_key)
    os.chmod(backup_path, stat.S_IRUSR | stat.S_IWUSR)

    # Write new key
    key_file.write_bytes(new_key)
    os.chmod(key_file, stat.S_IRUSR | stat.S_IWUSR)

    # Write re-encrypted files
    for path, blob in re_encrypted:
        path.write_bytes(blob)

    # Reset cached Fernet so it picks up the new key
    global _fernet
    _fernet = None

    logger.info("Key rotation complete: %d files re-encrypted, %d failed", len(re_encrypted), len(failed))
    return {
        "status": "success",
        "files_migrated": len(re_encrypted),
        "files_failed": len(failed),
        "failed_paths": failed,
        "backup": str(backup_path),
    }


def reset_fernet() -> None:
    """Reset cached Fernet instance. Used by tests."""
    global _fernet
    _fernet = None
