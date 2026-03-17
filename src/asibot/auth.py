"""API key authentication and user store.

Users are stored at ~/.asibot/users.json:
{
    "api_key_abc123": {
        "user_id": "jacob@asirobots.com",
        "name": "Jacob Malm",
        "api_key": "api_key_abc123",
        "created_at": "2026-03-17T12:00:00Z"
    }
}
"""

import json
import logging
import secrets
from datetime import datetime, timezone
from pathlib import Path

from asibot.config import settings

logger = logging.getLogger(__name__)

_users: dict[str, dict] = {}  # api_key -> user profile
_store_path: Path = settings.data_dir / "users.json"


def _load() -> None:
    global _users
    if _store_path.exists():
        try:
            _users = json.loads(_store_path.read_text())
        except Exception:
            _users = {}


def _save() -> None:
    settings.ensure_dirs()
    _store_path.write_text(json.dumps(_users, indent=2))


def create_user(email: str, name: str) -> dict:
    """Create a new user or return existing one by email."""
    _load()

    # Check if user already exists
    for key, user in _users.items():
        if user["user_id"] == email:
            logger.info("User already exists: %s", email)
            return user

    api_key = f"asb_{secrets.token_urlsafe(32)}"
    user = {
        "user_id": email,
        "name": name,
        "api_key": api_key,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _users[api_key] = user
    _save()
    logger.info("Created user: %s (%s)", name, email)
    return user


def get_user_by_key(api_key: str) -> dict | None:
    """Look up user by API key."""
    _load()
    return _users.get(api_key)


def get_user_by_email(email: str) -> dict | None:
    """Look up user by email."""
    _load()
    for user in _users.values():
        if user["user_id"] == email:
            return user
    return None


def list_users() -> list[dict]:
    """List all registered users."""
    _load()
    return list(_users.values())
