"""One-time migration from encrypted JSON files to SQLite.

Reads existing ~/.asibot/ file structure and populates the SQLite database.
Old files are renamed to .bak (not deleted) for rollback safety.
Auto-runs on first startup if old files exist and DB tables are empty.
"""

import json
import logging
from pathlib import Path

from asibot import db
from asibot.config import settings
from asibot.crypto import load_encrypted, encrypt_json

logger = logging.getLogger(__name__)


async def needs_migration() -> bool:
    """Check if migration from files to SQLite is needed."""
    users_file = settings.data_dir / "users.json"
    if not users_file.exists():
        return False
    # Check if DB already has users
    users = await db.list_users()
    return len(users) == 0


async def migrate_from_files() -> dict:
    """Migrate existing encrypted JSON files to SQLite.

    Returns summary: {users_migrated, credentials_migrated, preferences_migrated,
                      ms_tokens_migrated, errors}
    """
    summary = {
        "users_migrated": 0,
        "credentials_migrated": 0,
        "preferences_migrated": 0,
        "ms_tokens_migrated": 0,
        "errors": [],
    }

    # 1. Migrate users.json
    users_file = settings.data_dir / "users.json"
    if users_file.exists():
        try:
            users_data = load_encrypted(users_file)
            for api_key, user in users_data.items():
                if api_key.startswith("_"):
                    continue
                created = await db.create_user(
                    user_id=user["user_id"],
                    name=user.get("name", user["user_id"]),
                    api_key=user.get("api_key", api_key),
                    created_at=user.get("created_at", ""),
                )
                if created:
                    summary["users_migrated"] += 1
            _backup(users_file)
            logger.info("Migrated %d users from %s", summary["users_migrated"], users_file)
        except Exception as e:
            summary["errors"].append(f"users.json: {e}")
            logger.error("Failed to migrate users.json: %s", e)

    # 2. Migrate per-user data directories
    users_dir = settings.data_dir / "users"
    if users_dir.exists():
        for user_dir in users_dir.iterdir():
            if not user_dir.is_dir():
                continue
            user_email = _dir_to_email(user_dir.name)

            # Credentials
            creds_file = user_dir / "credentials.json"
            if creds_file.exists():
                try:
                    creds_data = load_encrypted(creds_file)
                    for service, creds in creds_data.items():
                        if service.startswith("_"):
                            continue
                        encrypted = encrypt_json(creds)
                        await db.set_credentials(user_email, service, encrypted)
                        summary["credentials_migrated"] += 1
                    _backup(creds_file)
                except Exception as e:
                    summary["errors"].append(f"{user_dir.name}/credentials.json: {e}")

            # Preferences
            prefs_file = user_dir / "preferences.json"
            if prefs_file.exists():
                try:
                    prefs_data = load_encrypted(prefs_file)
                    connectors = prefs_data.get("connectors", {})
                    for service, pref in connectors.items():
                        await db.set_service_prefs(
                            user_email, service,
                            enabled=pref.get("enabled", True),
                            mode=pref.get("mode", "read"),
                        )
                        summary["preferences_migrated"] += 1
                    _backup(prefs_file)
                except Exception as e:
                    summary["errors"].append(f"{user_dir.name}/preferences.json: {e}")

            # Microsoft token
            ms_file = user_dir / "microsoft_token.json"
            if ms_file.exists():
                try:
                    ms_data = load_encrypted(ms_file)
                    if ms_data:
                        encrypted = encrypt_json(ms_data)
                        await db.save_ms_token(user_email, encrypted)
                        summary["ms_tokens_migrated"] += 1
                    _backup(ms_file)
                except Exception as e:
                    summary["errors"].append(f"{user_dir.name}/microsoft_token.json: {e}")

    logger.info(
        "Migration complete: %d users, %d credentials, %d preferences, %d MS tokens, %d errors",
        summary["users_migrated"],
        summary["credentials_migrated"],
        summary["preferences_migrated"],
        summary["ms_tokens_migrated"],
        len(summary["errors"]),
    )
    return summary


def _backup(path: Path) -> None:
    """Rename file to .bak for rollback safety."""
    bak = path.with_suffix(path.suffix + ".bak")
    if not bak.exists():
        path.rename(bak)
        logger.debug("Backed up %s -> %s", path, bak)


def _dir_to_email(dirname: str) -> str:
    """Convert sanitized directory name back to email."""
    return dirname.replace("_at_", "@")
