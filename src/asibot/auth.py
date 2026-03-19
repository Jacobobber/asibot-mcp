"""API key authentication and user store.

All user data lives in PostgreSQL via the db module.
"""

import logging
import secrets
from datetime import datetime, timezone

from asibot.roles import VALID_ROLES

logger = logging.getLogger(__name__)


async def create_user(email: str, name: str, role: str | None = None) -> dict:
    """Create a new user or return existing one by email.

    If role is None, the first user gets 'admin', subsequent users get 'user'.
    """
    from asibot import db

    existing = await db.get_user_by_email(email)
    if existing:
        logger.info("User already exists: %s", email)
        return existing

    if role is None:
        users = await db.list_users()
        role = "admin" if len(users) == 0 else "user"

    api_key = f"asb_{secrets.token_urlsafe(32)}"
    created_at = datetime.now(timezone.utc).isoformat()
    await db.create_user(email, name, api_key, created_at, role)
    user = await db.get_user_by_email(email)
    logger.info("Created user: %s (%s) with role '%s'", name, email, role)
    return user


async def get_user_by_key(api_key: str) -> dict | None:
    """Look up user by API key."""
    from asibot import db
    return await db.get_user_by_key(api_key)


async def get_user_by_email(email: str) -> dict | None:
    """Look up user by email."""
    from asibot import db
    return await db.get_user_by_email(email)


async def get_role(email: str) -> str:
    """Get the role for a user. Defaults to 'user' if not found."""
    user = await get_user_by_email(email)
    if user:
        return user.get("role", "user")
    return "user"


async def set_role(email: str, role: str, *, admin_id: str | None = None) -> dict | None:
    """Set a user's role. Returns updated user or None if not found.

    When *admin_id* is provided the role change and an audit event are
    written inside a single database transaction so they either both
    persist or neither does.
    """
    from asibot import audit, db

    if role not in VALID_ROLES:
        audit.log_event(
            user_id=email,
            event="role_change",
            tool="admin",
            args={
                "attempted_role": role,
                "error": f"Invalid role. Must be one of {sorted(VALID_ROLES)}",
                "changed_by": admin_id or "unknown",
            },
            service="rbac",
            success=False,
        )
        raise ValueError(f"Invalid role: {role}. Must be one of {VALID_ROLES}")

    user = await db.get_user_by_email(email)
    if not user:
        audit.log_event(
            user_id=email,
            event="role_change",
            tool="admin",
            args={
                "new_role": role,
                "error": "User not found",
                "changed_by": admin_id or "unknown",
            },
            service="rbac",
            success=False,
        )
        return None

    if admin_id is not None:
        updated = await db.set_role_with_audit(email, role, admin_id=admin_id)
    else:
        updated = await db.set_role(email, role)

    if updated:
        old_role = user.get("role", "user")
        logger.info("Set role '%s' for user %s (was '%s')", role, email, old_role)
        audit.log_event(
            user_id=email,
            event="role_change",
            tool="admin",
            args={
                "old_role": old_role,
                "new_role": role,
                "changed_by": admin_id or "unknown",
            },
            service="rbac",
            success=True,
        )
    return updated


async def rotate_key(email: str) -> dict | None:
    """Generate a new API key for a user. Invalidates the old key."""
    from asibot import db

    new_key = f"asb_{secrets.token_urlsafe(32)}"
    rotated_at = datetime.now(timezone.utc).isoformat()
    updated = await db.rotate_key(email, new_key, rotated_at)
    if updated:
        logger.info("Rotated API key for user %s", email)
    return updated


async def list_users() -> list[dict]:
    """List all registered users."""
    from asibot import db
    return await db.list_users()


async def count_admins() -> int:
    """Count the number of admin users."""
    users = await list_users()
    return sum(1 for u in users if u.get("role") == "admin")
