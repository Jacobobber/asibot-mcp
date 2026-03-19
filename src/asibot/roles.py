"""Role-based access control definitions and helpers."""

from enum import IntEnum

VALID_ROLES = frozenset({"admin", "user", "readonly"})


class Role(IntEnum):
    readonly = 0
    user = 1
    admin = 2


def has_permission(user_role: str, required_role: str) -> bool:
    """Check if user_role meets or exceeds required_role in the hierarchy."""
    try:
        return Role[user_role] >= Role[required_role]
    except KeyError:
        return False
