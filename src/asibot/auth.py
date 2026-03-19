"""API key authentication and user store.

Users are stored encrypted at ~/.asibot/users.json.
"""

import logging
import secrets
from datetime import datetime, timezone
from pathlib import Path

from asibot.config import settings
from asibot.crypto import load_encrypted, save_encrypted

logger = logging.getLogger(__name__)

_users: dict[str, dict] = {}  # api_key -> user profile
_store_path: Path = settings.data_dir / "users.json"


def _load() -> None:
    global _users
    _users = load_encrypted(_store_path)


def _save() -> None:
    settings.ensure_dirs()
    save_encrypted(_store_path, _users)


def create_user(email: str, name: str, role: str = "user") -> dict:
    """Create a new user or return existing one by email.

    If the user already exists and the role differs, update it (supports
    re-setup syncing from M365 groups).
    """
    _load()

    # Check if user already exists
    for key, user in _users.items():
        if user["user_id"] == email:
            if user.get("role") != role:
                user["role"] = role
                _save()
                logger.info("Updated role for %s to %s", email, role)
            else:
                logger.info("User already exists: %s", email)
            return user

    api_key = f"asb_{secrets.token_urlsafe(32)}"
    user = {
        "user_id": email,
        "name": name,
        "api_key": api_key,
        "role": role,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _users[api_key] = user
    _save()
    logger.info("Created user: %s (%s) role=%s", name, email, role)
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


def rotate_key(email: str) -> dict | None:
    """Generate a new API key for a user. Invalidates the old key.

    Returns the updated user dict, or None if user not found.
    """
    _load()
    for old_key, user in list(_users.items()):
        if user["user_id"] == email:
            new_key = f"asb_{secrets.token_urlsafe(32)}"
            user["api_key"] = new_key
            user["key_rotated_at"] = datetime.now(timezone.utc).isoformat()
            del _users[old_key]
            _users[new_key] = user
            _save()
            logger.info("Rotated API key for user %s", email)
            return user
    return None


def list_users() -> list[dict]:
    """List all registered users."""
    _load()
    return list(_users.values())


def get_role(email: str) -> str:
    """Get the role for a user. Returns 'user' if not set or user not found."""
    user = get_user_by_email(email)
    if not user:
        return "user"
    return user.get("role", "user")


def set_role(email: str, role: str) -> dict | None:
    """Set the role for a user. Returns updated user or None if not found."""
    from asibot.roles import VALID_ROLES

    if role not in VALID_ROLES:
        return None
    user = get_user_by_email(email)
    if not user:
        return None
    user["role"] = role
    _save()
    return user


def count_admins() -> int:
    """Count users with admin role."""
    _load()
    return sum(1 for u in _users.values() if u.get("role") == "admin")
