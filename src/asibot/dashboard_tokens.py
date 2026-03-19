"""Per-user dashboard token store, shared between the MCP server and the dashboard daemon thread.

The MCP server writes tokens (on login/logout) while the dashboard daemon thread
reads them to authenticate WebSocket and HTTP requests.  All public functions
acquire ``_lock`` so the store is safe to use from any number of threads
concurrently.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TokenEntry:
    user_id: str
    user_name: str
    role: str
    created_at: float
    expires_at: float


# ---------------------------------------------------------------------------
# Module-level state
# ---------------------------------------------------------------------------

_tokens: dict[str, TokenEntry] = {}
_lock = threading.Lock()
_MAX_TOKENS = 10_000


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def create_token(
    user_id: str,
    user_name: str,
    role: str,
    ttl_seconds: int = 86400,
) -> str:
    """Create a new dashboard token and return the raw token string.

    If the store is at capacity (``_MAX_TOKENS``), the oldest token (by
    ``created_at``) is evicted before the new one is inserted.
    """
    token = secrets.token_urlsafe(32)
    now = time.time()
    entry = TokenEntry(
        user_id=user_id,
        user_name=user_name,
        role=role,
        created_at=now,
        expires_at=now + ttl_seconds,
    )
    with _lock:
        # Evict oldest token when at the hard cap.
        if len(_tokens) >= _MAX_TOKENS:
            oldest_key = min(_tokens, key=lambda k: _tokens[k].created_at)
            del _tokens[oldest_key]
        _tokens[token] = entry
    return token


def validate_token(token: str) -> TokenEntry | None:
    """Return the ``TokenEntry`` if *token* exists and has not expired, else ``None``."""
    with _lock:
        entry = _tokens.get(token)
    if entry is None:
        return None
    if time.time() > entry.expires_at:
        return None
    return entry


def revoke_token(token: str) -> bool:
    """Remove a single token.  Returns ``True`` if the token existed."""
    with _lock:
        return _tokens.pop(token, None) is not None


def revoke_user_tokens(user_id: str) -> int:
    """Remove **all** tokens belonging to *user_id*.  Returns the count removed."""
    with _lock:
        to_remove = [k for k, v in _tokens.items() if v.user_id == user_id]
        for k in to_remove:
            del _tokens[k]
    return len(to_remove)


def cleanup_expired() -> int:
    """Remove every expired token.  Returns the count removed."""
    now = time.time()
    with _lock:
        to_remove = [k for k, v in _tokens.items() if now > v.expires_at]
        for k in to_remove:
            del _tokens[k]
    return len(to_remove)


def active_token_count() -> int:
    """Return the number of tokens whose ``expires_at`` is still in the future."""
    now = time.time()
    with _lock:
        return sum(1 for v in _tokens.values() if now <= v.expires_at)


def reset() -> None:
    """Clear all tokens.  Intended for use in tests."""
    with _lock:
        _tokens.clear()
