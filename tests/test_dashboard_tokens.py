"""Tests for the per-user dashboard token store."""

import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "src")

from asibot.dashboard_tokens import (
    _MAX_TOKENS,
    active_token_count,
    cleanup_expired,
    create_token,
    reset,
    revoke_token,
    revoke_user_tokens,
    validate_token,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def setup_function():
    """Ensure a clean token store before every test."""
    reset()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_create_and_validate():
    token = create_token("u1", "Alice", "admin")
    entry = validate_token(token)
    assert entry is not None
    assert entry.user_id == "u1"
    assert entry.role == "admin"


def test_expired_token_rejected():
    token = create_token("u1", "Alice", "admin", ttl_seconds=0)
    time.sleep(0.01)
    assert validate_token(token) is None


def test_revoke_token():
    token = create_token("u1", "Alice", "admin")
    assert revoke_token(token) is True
    assert validate_token(token) is None
    assert revoke_token(token) is False


def test_revoke_user_tokens():
    tokens = [create_token("u1", "Alice", "admin") for _ in range(3)]
    assert revoke_user_tokens("u1") == 3
    for t in tokens:
        assert validate_token(t) is None


def test_cleanup_expired():
    create_token("u1", "Alice", "admin", ttl_seconds=0)
    create_token("u2", "Bob", "viewer", ttl_seconds=0)
    valid_token = create_token("u3", "Carol", "editor", ttl_seconds=3600)
    time.sleep(0.01)
    assert cleanup_expired() == 2
    assert validate_token(valid_token) is not None


def test_max_cap():
    for i in range(_MAX_TOKENS + 1):
        create_token(f"u{i}", f"User{i}", "viewer", ttl_seconds=3600)
    assert active_token_count() <= _MAX_TOKENS


def test_thread_safety():
    results: list[str] = []

    def _create(n: int) -> list[str]:
        return [
            create_token(f"u{n}_{i}", f"User{n}_{i}", "viewer", ttl_seconds=3600)
            for i in range(10)
        ]

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(_create, n) for n in range(10)]
        for f in futures:
            results.extend(f.result())

    assert len(results) == 100
    for token in results:
        assert validate_token(token) is not None


def test_reset():
    for i in range(5):
        create_token(f"u{i}", f"User{i}", "admin")
    assert active_token_count() > 0
    reset()
    assert active_token_count() == 0
