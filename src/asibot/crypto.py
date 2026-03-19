"""Fernet encryption for credential storage.

Master key is generated once and stored at ~/.asibot/master.key with
restricted file permissions (owner-only read/write). All credential and
token files are encrypted at rest using this key.

Supports optional external KMS providers (AWS KMS, HashiCorp Vault) for
master-key management. Falls back to local file if no KMS is configured.

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
_cached_master_key: bytes | None = None


def _key_path() -> Path:
    return settings.data_dir / "master.key"


def _reset_master_key_cache() -> None:
    """Reset the cached master key. Used by tests."""
    global _cached_master_key
    _cached_master_key = None


def _fetch_key_from_aws_kms() -> bytes:
    """Fetch or decrypt master key via AWS KMS."""
    try:
        import boto3  # noqa: F811
    except ImportError:
        raise RuntimeError(
            "boto3 is required for AWS KMS integration. "
            "Install it with: pip install boto3"
        )

    key_file = _key_path()
    client = boto3.client("kms")

    if key_file.exists():
        # Decrypt the locally-stored encrypted key using KMS
        encrypted_key = key_file.read_bytes()
        response = client.decrypt(
            CiphertextBlob=encrypted_key,
            KeyId=settings.kms_key_id,
        )
        return response["Plaintext"]
    else:
        # Generate a new data key via KMS
        response = client.generate_data_key(
            KeyId=settings.kms_key_id,
            KeySpec="AES_256",
        )
        # Store the encrypted copy locally
        settings.ensure_dirs()
        key_file.write_bytes(response["CiphertextBlob"])
        os.chmod(key_file, stat.S_IRUSR | stat.S_IWUSR)
        logger.info("Generated new KMS-encrypted master key at %s", key_file)
        return response["Plaintext"]


def _fetch_key_from_vault() -> bytes:
    """Fetch master key from HashiCorp Vault via HTTP."""
    try:
        import urllib.request
        import urllib.error
    except ImportError:
        raise RuntimeError("urllib is required for Vault integration")

    if not settings.vault_addr:
        raise RuntimeError(
            "ASIBOT_VAULT_ADDR must be set when using Vault KMS provider"
        )
    if not settings.vault_token:
        raise RuntimeError(
            "ASIBOT_VAULT_TOKEN must be set when using Vault KMS provider"
        )

    url = f"{settings.vault_addr.rstrip('/')}/v1/{settings.kms_key_id.lstrip('/')}"
    req = urllib.request.Request(
        url,
        headers={"X-Vault-Token": settings.vault_token},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Failed to fetch key from Vault: {exc}") from exc

    # Vault KV v2 nests under data.data; v1 nests under data
    data = payload.get("data", {})
    if "data" in data:
        data = data["data"]

    key_value = data.get("key")
    if not key_value:
        raise RuntimeError(
            f"Vault path '{settings.kms_key_id}' did not return a 'key' field"
        )

    return key_value.encode("utf-8") if isinstance(key_value, str) else key_value


def get_master_key() -> bytes:
    """Retrieve the master encryption key, using KMS if configured.

    Provider resolution order:
      1. ``kms_provider == "aws"``  -- AWS KMS ``GenerateDataKey`` / ``Decrypt``
      2. ``kms_provider == "vault"`` -- HashiCorp Vault HTTP API
      3. ``kms_provider == ""``     -- local file (``~/.asibot/master.key``)

    The key is cached in-process after the first successful load.
    """
    global _cached_master_key
    if _cached_master_key is not None:
        return _cached_master_key

    provider = settings.kms_provider.lower().strip()

    if provider == "aws":
        logger.info("Loading master key from AWS KMS (key: %s)", settings.kms_key_id)
        import base64

        raw = _fetch_key_from_aws_kms()
        # AWS returns raw 32-byte AES key; Fernet needs url-safe base64
        _cached_master_key = base64.urlsafe_b64encode(raw)
        return _cached_master_key

    if provider == "vault":
        logger.info(
            "Loading master key from HashiCorp Vault (%s)", settings.vault_addr
        )
        _cached_master_key = _fetch_key_from_vault()
        return _cached_master_key

    if provider == "":
        logger.info("Loading master key from local file")
        key_file = _key_path()
        if key_file.exists():
            _cached_master_key = key_file.read_bytes().strip()
        else:
            _cached_master_key = Fernet.generate_key()
            settings.ensure_dirs()
            key_file.write_bytes(_cached_master_key)
            os.chmod(key_file, stat.S_IRUSR | stat.S_IWUSR)
            logger.info("Generated new master key at %s", key_file)
        return _cached_master_key

    raise ValueError(
        f"Unknown kms_provider '{provider}'. "
        "Supported values: 'aws', 'vault', or '' (local file)."
    )


def _get_fernet() -> Fernet:
    """Get or create the Fernet instance, generating the master key if needed."""
    global _fernet
    if _fernet is not None:
        return _fernet

    key = get_master_key()
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

    # Reset cached Fernet and master key so it picks up the new key
    global _fernet
    _fernet = None
    _reset_master_key_cache()

    logger.info("Key rotation complete: %d files re-encrypted, %d failed", len(re_encrypted), len(failed))
    return {
        "status": "success",
        "files_migrated": len(re_encrypted),
        "files_failed": len(failed),
        "failed_paths": failed,
        "backup": str(backup_path),
    }


def reset_fernet() -> None:
    """Reset cached Fernet instance and master key cache. Used by tests."""
    global _fernet
    _fernet = None
    _reset_master_key_cache()
