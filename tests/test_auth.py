"""Tests for user authentication and API key store (async / DB-backed)."""

from unittest.mock import AsyncMock, patch

import pytest

from asibot import audit, auth, db as real_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _InMemoryDB:
    """Patches individual functions on the real asibot.db module."""

    def __init__(self):
        self.users: dict[str, dict] = {}
        self._patches = []

    def __enter__(self):
        funcs = {
            "create_user": self._create_user,
            "get_user_by_email": self._get_user_by_email,
            "get_user_by_key": self._get_user_by_key,
            "list_users": self._list_users,
            "set_role": self._set_role,
            "set_role_with_audit": self._set_role_with_audit,
            "rotate_key": self._rotate_key,
        }
        for name, impl in funcs.items():
            p = patch.object(real_db, name, new=AsyncMock(side_effect=impl))
            p.start()
            self._patches.append(p)
        return self

    def __exit__(self, *args):
        for p in reversed(self._patches):
            p.stop()
        self._patches.clear()

    async def _create_user(self, email, name, api_key, created_at, role):
        self.users[email] = {
            "user_id": email, "name": name, "api_key": api_key,
            "created_at": created_at, "role": role,
        }

    async def _get_user_by_email(self, email):
        return self.users.get(email)

    async def _get_user_by_key(self, key):
        for u in self.users.values():
            if u["api_key"] == key:
                return u
        return None

    async def _list_users(self):
        return list(self.users.values())

    async def _set_role(self, email, role):
        if email not in self.users:
            return None
        self.users[email]["role"] = role
        return dict(self.users[email])

    async def _set_role_with_audit(self, email, role, *, admin_id=None):
        if email not in self.users:
            return None
        self.users[email]["role"] = role
        return dict(self.users[email])

    async def _rotate_key(self, email, new_key, rotated_at):
        if email not in self.users:
            return None
        self.users[email]["api_key"] = new_key
        self.users[email]["key_rotated_at"] = rotated_at
        return dict(self.users[email])


# ---------------------------------------------------------------------------
# Basic auth tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_user():
    with _InMemoryDB():
        user = await auth.create_user("alice@example.com", "Alice")
        assert user["user_id"] == "alice@example.com"
        assert user["name"] == "Alice"
        assert user["api_key"].startswith("asb_")


@pytest.mark.asyncio
async def test_create_user_idempotent():
    with _InMemoryDB():
        u1 = await auth.create_user("bob@example.com", "Bob")
        u2 = await auth.create_user("bob@example.com", "Bob")
        assert u1["api_key"] == u2["api_key"]


@pytest.mark.asyncio
async def test_get_user_by_key():
    with _InMemoryDB():
        user = await auth.create_user("carol@example.com", "Carol")
        found = await auth.get_user_by_key(user["api_key"])
        assert found is not None
        assert found["user_id"] == "carol@example.com"


@pytest.mark.asyncio
async def test_get_user_by_key_not_found():
    with _InMemoryDB():
        assert await auth.get_user_by_key("nonexistent") is None


@pytest.mark.asyncio
async def test_get_user_by_email():
    with _InMemoryDB():
        await auth.create_user("dave@example.com", "Dave")
        found = await auth.get_user_by_email("dave@example.com")
        assert found is not None
        assert found["name"] == "Dave"


@pytest.mark.asyncio
async def test_get_user_by_email_not_found():
    with _InMemoryDB():
        assert await auth.get_user_by_email("ghost@x.com") is None


@pytest.mark.asyncio
async def test_first_user_gets_admin():
    with _InMemoryDB():
        user = await auth.create_user("first@example.com", "First")
        assert user["role"] == "admin"


@pytest.mark.asyncio
async def test_subsequent_users_get_user():
    with _InMemoryDB():
        await auth.create_user("first@example.com", "First")
        user2 = await auth.create_user("second@example.com", "Second")
        assert user2["role"] == "user"


@pytest.mark.asyncio
async def test_rotate_key():
    with _InMemoryDB():
        user = await auth.create_user("rotate@example.com", "Rotate")
        old_key = user["api_key"]
        rotated = await auth.rotate_key("rotate@example.com")
        assert rotated is not None
        assert rotated["api_key"] != old_key
        assert rotated["api_key"].startswith("asb_")


@pytest.mark.asyncio
async def test_rotate_key_not_found():
    with _InMemoryDB():
        assert await auth.rotate_key("nobody@example.com") is None


# ---------------------------------------------------------------------------
# Role management tests
# ---------------------------------------------------------------------------


class TestSetRole:
    """Tests for set_role() with audit trail."""

    @pytest.mark.asyncio
    async def test_set_role_success(self):
        with _InMemoryDB():
            await auth.create_user("alice@example.com", "Alice")
            result = await auth.set_role("alice@example.com", "user", admin_id="boss@example.com")
            assert result is not None
            assert result["role"] == "user"

    @pytest.mark.asyncio
    async def test_set_role_returns_none_for_unknown_user(self):
        with _InMemoryDB(), patch.object(audit, "log_event"):
            result = await auth.set_role("nobody@example.com", "admin")
            assert result is None

    @pytest.mark.asyncio
    async def test_set_role_invalid_role_raises(self):
        with _InMemoryDB(), patch.object(audit, "log_event"):
            await auth.create_user("alice@example.com", "Alice")
            with pytest.raises(ValueError, match="Invalid role"):
                await auth.set_role("alice@example.com", "superuser")

    @pytest.mark.asyncio
    async def test_set_role_all_valid_roles(self):
        with _InMemoryDB():
            await auth.create_user("alice@example.com", "Alice")
            for role in auth.VALID_ROLES:
                result = await auth.set_role("alice@example.com", role)
                assert result is not None
                assert result["role"] == role

    @pytest.mark.asyncio
    async def test_get_role_default(self):
        with _InMemoryDB():
            await auth.create_user("alice@example.com", "Alice")
            assert await auth.get_role("alice@example.com") == "admin"

    @pytest.mark.asyncio
    async def test_get_role_after_set(self):
        with _InMemoryDB():
            await auth.create_user("alice@example.com", "Alice")
            await auth.set_role("alice@example.com", "readonly")
            assert await auth.get_role("alice@example.com") == "readonly"

    @pytest.mark.asyncio
    async def test_get_role_unknown_user(self):
        with _InMemoryDB():
            assert await auth.get_role("nobody@example.com") == "user"

    @pytest.mark.asyncio
    async def test_count_admins(self):
        with _InMemoryDB():
            await auth.create_user("alice@example.com", "Alice")  # admin (first)
            await auth.create_user("bob@example.com", "Bob")  # user
            assert await auth.count_admins() == 1
            await auth.set_role("bob@example.com", "admin")
            assert await auth.count_admins() == 2


class TestRoleChangeAuditTrail:
    """Tests that role changes produce audit log entries."""

    @pytest.mark.asyncio
    async def test_successful_role_change_audited(self):
        audit_entries = []

        def _capture_event(user_id, event, **kwargs):
            audit_entries.append({"user_id": user_id, "event": event, **kwargs})

        with _InMemoryDB(), patch.object(audit, "log_event", side_effect=_capture_event):
            await auth.create_user("alice@example.com", "Alice")
            await auth.set_role("alice@example.com", "readonly", admin_id="boss@example.com")

        success_entries = [e for e in audit_entries if e.get("success") is True]
        assert len(success_entries) == 1
        entry = success_entries[0]
        assert entry["user_id"] == "alice@example.com"
        assert entry["event"] == "role_change"
        assert entry["tool"] == "admin"
        assert entry["service"] == "rbac"
        assert entry["args"]["new_role"] == "readonly"
        assert entry["args"]["changed_by"] == "boss@example.com"

    @pytest.mark.asyncio
    async def test_failed_role_change_user_not_found_audited(self):
        audit_entries = []

        def _capture_event(user_id, event, **kwargs):
            audit_entries.append({"user_id": user_id, "event": event, **kwargs})

        with _InMemoryDB(), patch.object(audit, "log_event", side_effect=_capture_event):
            result = await auth.set_role("ghost@example.com", "admin", admin_id="boss@example.com")

        assert result is None
        assert len(audit_entries) == 1
        entry = audit_entries[0]
        assert entry["event"] == "role_change"
        assert entry["success"] is False
        assert "User not found" in entry["args"]["error"]

    @pytest.mark.asyncio
    async def test_invalid_role_audited(self):
        audit_entries = []

        def _capture_event(user_id, event, **kwargs):
            audit_entries.append({"user_id": user_id, "event": event, **kwargs})

        with _InMemoryDB(), patch.object(audit, "log_event", side_effect=_capture_event):
            await auth.create_user("alice@example.com", "Alice")
            with pytest.raises(ValueError):
                await auth.set_role("alice@example.com", "superuser", admin_id="boss@example.com")

        assert len(audit_entries) == 1
        entry = audit_entries[0]
        assert entry["event"] == "role_change"
        assert entry["success"] is False
        assert "Invalid role" in entry["args"]["error"]

    @pytest.mark.asyncio
    async def test_role_change_without_admin_id(self):
        audit_entries = []

        def _capture_event(user_id, event, **kwargs):
            audit_entries.append({"user_id": user_id, "event": event, **kwargs})

        with _InMemoryDB(), patch.object(audit, "log_event", side_effect=_capture_event):
            await auth.create_user("alice@example.com", "Alice")
            await auth.set_role("alice@example.com", "readonly")

        success_entries = [e for e in audit_entries if e.get("success") is True]
        assert len(success_entries) == 1
        assert success_entries[0]["args"]["changed_by"] == "unknown"
