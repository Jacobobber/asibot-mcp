"""Session store abstraction with in-memory and Redis backends.

Provides a common interface for session caching and auth-failure tracking,
allowing the server to survive restarts without forcing all users to
re-authenticate.

Usage:
    from asibot.session_store import create_session_store
    store = create_session_store()  # reads from config
"""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from collections import OrderedDict

logger = logging.getLogger(__name__)


class SessionStore(ABC):
    """Abstract base class for session storage backends."""

    @abstractmethod
    def get_session(self, session_id: str) -> tuple[str, float] | None:
        """Look up a session by ID.

        Returns (user_id, created_at_timestamp) or None if not found / expired.
        """

    @abstractmethod
    def put_session(
        self, session_id: str, user_id: str, ttl: int, timestamp: float | None = None
    ) -> None:
        """Store or refresh a session.

        Parameters
        ----------
        session_id : str
            Unique session identifier.
        user_id : str
            The authenticated user email.
        ttl : int
            Time-to-live in seconds from *now*.
        timestamp : float | None
            Optional creation timestamp; defaults to ``time.time()``.
        """

    @abstractmethod
    def delete_session(self, session_id: str) -> None:
        """Remove a single session."""

    @abstractmethod
    def delete_user_sessions(self, user_id: str) -> int:
        """Remove all sessions for a given user. Returns count deleted."""

    @abstractmethod
    def evict_expired(self) -> None:
        """Remove all expired sessions (housekeeping)."""

    # --- Auth failure tracking ---

    @abstractmethod
    def record_auth_failure(self, key_prefix: str) -> None:
        """Record a failed authentication attempt."""

    @abstractmethod
    def is_rate_limited(self, key_prefix: str, window: int, max_failures: int) -> bool:
        """Check whether a key prefix has exceeded the failure threshold."""


# ---------------------------------------------------------------------------
# In-memory implementation (default fallback)
# ---------------------------------------------------------------------------


class InMemorySessionStore(SessionStore):
    """LRU in-memory session store — the original behaviour wrapped in the ABC."""

    def __init__(self, max_sessions: int = 10_000, default_ttl: int = 3600) -> None:
        self._sessions: OrderedDict[str, tuple[str, float]] = OrderedDict()
        self._max_sessions = max_sessions
        self._default_ttl = default_ttl
        self._auth_failures: dict[str, list[float]] = {}

    # --- sessions ---

    def get_session(self, session_id: str) -> tuple[str, float] | None:
        entry = self._sessions.get(session_id)
        if entry is None:
            return None
        user_id, ts = entry
        if time.time() - ts > self._default_ttl:
            del self._sessions[session_id]
            return None
        # Refresh: move to end (LRU)
        self._sessions[session_id] = (user_id, time.time())
        self._sessions.move_to_end(session_id)
        return user_id, ts

    def put_session(
        self, session_id: str, user_id: str, ttl: int, timestamp: float | None = None
    ) -> None:
        ts = timestamp if timestamp is not None else time.time()
        self._sessions.pop(session_id, None)
        if len(self._sessions) >= self._max_sessions:
            self.evict_expired()
        while len(self._sessions) >= self._max_sessions:
            self._sessions.popitem(last=False)
        self._sessions[session_id] = (user_id, ts)

    def delete_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def delete_user_sessions(self, user_id: str) -> int:
        stale = [sid for sid, (uid, _) in self._sessions.items() if uid == user_id]
        for sid in stale:
            del self._sessions[sid]
        return len(stale)

    def evict_expired(self) -> None:
        now = time.time()
        expired = [
            k for k, (_, ts) in self._sessions.items() if now - ts > self._default_ttl
        ]
        for k in expired:
            del self._sessions[k]

    # --- auth failure tracking ---

    def record_auth_failure(self, key_prefix: str) -> None:
        self._auth_failures.setdefault(key_prefix, []).append(time.time())

    def is_rate_limited(self, key_prefix: str, window: int, max_failures: int) -> bool:
        now = time.time()
        cutoff = now - window
        entries = self._auth_failures.get(key_prefix)
        if not entries:
            return False
        while entries and entries[0] < cutoff:
            entries.pop(0)
        if not entries:
            del self._auth_failures[key_prefix]
            return False
        return len(entries) >= max_failures


# ---------------------------------------------------------------------------
# Redis implementation
# ---------------------------------------------------------------------------


class RedisSessionStore(SessionStore):
    """Redis-backed session store using ``redis[hiredis]``.

    Key layout::

        asibot:session:<session_id>  ->  JSON {"user_id": ..., "ts": ...}
        asibot:authfail:<key_prefix> ->  Redis sorted set (score=timestamp)
    """

    _KEY_PREFIX = "asibot:session:"
    _FAIL_PREFIX = "asibot:authfail:"

    def __init__(self, redis_url: str, default_ttl: int = 3600) -> None:
        import redis as _redis

        self._default_ttl = default_ttl
        self._redis: _redis.Redis = _redis.from_url(
            redis_url, decode_responses=True
        )
        logger.info("RedisSessionStore connected (%s)", redis_url.split("@")[-1])

    def _skey(self, session_id: str) -> str:
        return f"{self._KEY_PREFIX}{session_id}"

    def _fkey(self, key_prefix: str) -> str:
        return f"{self._FAIL_PREFIX}{key_prefix}"

    # --- sessions ---

    def get_session(self, session_id: str) -> tuple[str, float] | None:
        raw = self._redis.get(self._skey(session_id))
        if raw is None:
            return None
        data = json.loads(raw)
        user_id = data["user_id"]
        ts = data["ts"]
        # Refresh TTL on access
        self._redis.expire(self._skey(session_id), self._default_ttl)
        return user_id, ts

    def put_session(
        self, session_id: str, user_id: str, ttl: int, timestamp: float | None = None
    ) -> None:
        ts = timestamp if timestamp is not None else time.time()
        payload = json.dumps({"user_id": user_id, "ts": ts})
        self._redis.set(self._skey(session_id), payload, ex=ttl)

    def delete_session(self, session_id: str) -> None:
        self._redis.delete(self._skey(session_id))

    def delete_user_sessions(self, user_id: str) -> int:
        """Scan for all sessions belonging to a user and delete them.

        Note: this uses SCAN which is safe in production (no KEYS blocking).
        """
        count = 0
        cursor: int | str = 0
        pattern = f"{self._KEY_PREFIX}*"
        while True:
            cursor, keys = self._redis.scan(cursor=int(cursor), match=pattern, count=200)
            for key in keys:
                raw = self._redis.get(key)
                if raw:
                    data = json.loads(raw)
                    if data.get("user_id") == user_id:
                        self._redis.delete(key)
                        count += 1
            if cursor == 0:
                break
        return count

    def evict_expired(self) -> None:
        # Redis handles expiry natively via TTL — nothing to do.
        pass

    # --- auth failure tracking ---

    def record_auth_failure(self, key_prefix: str) -> None:
        now = time.time()
        fkey = self._fkey(key_prefix)
        pipe = self._redis.pipeline()
        pipe.zadd(fkey, {str(now): now})
        # Expire the sorted set after 10 minutes (generous upper bound)
        pipe.expire(fkey, 600)
        pipe.execute()

    def is_rate_limited(self, key_prefix: str, window: int, max_failures: int) -> bool:
        now = time.time()
        cutoff = now - window
        fkey = self._fkey(key_prefix)
        # Remove stale entries
        self._redis.zremrangebyscore(fkey, "-inf", cutoff)
        count = self._redis.zcard(fkey)
        return count >= max_failures


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_session_store() -> SessionStore:
    """Create a session store based on application config.

    Reads ``ASIBOT_SESSION_BACKEND`` ("memory" | "redis") and
    ``ASIBOT_REDIS_URL`` from :mod:`asibot.config`.
    """
    from asibot.config import settings

    backend = settings.session_backend
    if backend == "redis":
        url = settings.redis_url
        if not url:
            logger.warning(
                "ASIBOT_SESSION_BACKEND=redis but ASIBOT_REDIS_URL is empty; "
                "falling back to in-memory session store"
            )
            return InMemorySessionStore(default_ttl=settings.session_ttl)
        try:
            store = RedisSessionStore(redis_url=url, default_ttl=settings.session_ttl)
            # Quick connectivity check
            store._redis.ping()
            return store
        except Exception as exc:
            logger.error(
                "Failed to connect to Redis at %s: %s — falling back to in-memory",
                url,
                exc,
            )
            return InMemorySessionStore(default_ttl=settings.session_ttl)

    return InMemorySessionStore(
        default_ttl=settings.session_ttl,
    )
