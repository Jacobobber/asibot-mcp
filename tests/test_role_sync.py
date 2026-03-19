"""Tests for role support in user authentication."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from asibot import auth


# Helper to create a mock DB module that simulates an in-memory user store
def _mock_db():
    """Create a mock db module with an in-memory user store."""
    users = {}  # email -> user dict
    mock = MagicMock()

    async def _get_user_by_email(email):
        return users.get(email)

    async def _create_user(email, name, api_key, created_at, role):
        users[email] = {
            "user_id": email,
            "name": name,
            "api_key": api_key,
            "created_at": created_at,
            "role": role,
        }

    async def _list_users():
        return list(users.values())

    async def _set_role(email, role):
        if email not in users:
            return None
        users[email]["role"] = role
        return users[email]

    async def _set_role_with_audit(email, role, *, admin_id=None):
        return await _set_role(email, role)

    mock.get_user_by_email = AsyncMock(side_effect=_get_user_by_email)
    mock.create_user = AsyncMock(side_effect=_create_user)
    mock.list_users = AsyncMock(side_effect=_list_users)
    mock.set_role = AsyncMock(side_effect=_set_role)
    mock.set_role_with_audit = AsyncMock(side_effect=_set_role_with_audit)
    mock._users = users  # expose for test inspection
    return mock


@pytest.mark.asyncio
async def test_get_role_default():
    """User without role field returns 'user'."""
    db = _mock_db()
    with patch("asibot.db", db):
        user = await auth.create_user("nofield@example.com", "NoField")
        # Manually strip the role field to simulate a legacy user
        del db._users["nofield@example.com"]["role"]
        assert await auth.get_role("nofield@example.com") == "user"


@pytest.mark.asyncio
async def test_create_user_with_role():
    """create_user with role='admin' stores and returns admin role."""
    db = _mock_db()
    with patch("asibot.db", db):
        user = await auth.create_user("admin@example.com", "Admin", role="admin")
        assert user["role"] == "admin"
        assert await auth.get_role("admin@example.com") == "admin"


@pytest.mark.asyncio
async def test_set_role():
    """set_role updates an existing user's role."""
    db = _mock_db()
    mock_audit = MagicMock()
    mock_audit.log_event = MagicMock()
    with patch("asibot.db", db), patch("asibot.audit", mock_audit):
        await auth.create_user("changeme@example.com", "ChangeMe", role="user")
        assert await auth.get_role("changeme@example.com") == "user"

        result = await auth.set_role("changeme@example.com", "admin")
        assert result is not None
        assert result["role"] == "admin"
        assert await auth.get_role("changeme@example.com") == "admin"


@pytest.mark.asyncio
async def test_set_role_invalid():
    """set_role with an invalid role raises ValueError."""
    db = _mock_db()
    mock_audit = MagicMock()
    mock_audit.log_event = MagicMock()
    with patch("asibot.db", db), patch("asibot.audit", mock_audit):
        await auth.create_user("valid@example.com", "Valid", role="user")
        with pytest.raises(ValueError, match="Invalid role"):
            await auth.set_role("valid@example.com", "superadmin")
        # Role should remain unchanged
        assert await auth.get_role("valid@example.com") == "user"


@pytest.mark.asyncio
async def test_count_admins():
    """count_admins returns the correct number of admin users."""
    db = _mock_db()
    with patch("asibot.db", db):
        await auth.create_user("admin1@example.com", "Admin1", role="admin")
        await auth.create_user("admin2@example.com", "Admin2", role="admin")
        await auth.create_user("regular@example.com", "Regular", role="user")
        assert await auth.count_admins() == 2


@pytest.mark.asyncio
async def test_role_updated_on_re_setup():
    """Calling create_user again with same email returns the existing user."""
    db = _mock_db()
    with patch("asibot.db", db):
        user1 = await auth.create_user("sync@example.com", "Sync", role="user")
        assert user1["role"] == "user"

        # create_user returns existing user unchanged (no role update)
        user2 = await auth.create_user("sync@example.com", "Sync", role="admin")
        # Existing user is returned as-is
        assert user2["role"] == "user"
        assert user1["api_key"] == user2["api_key"]
