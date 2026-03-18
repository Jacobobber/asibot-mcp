"""Distributed cache for S2S tokens and rate limiting.

Follows the same ABC + InMemory + Redis pattern as session_store.
In-memory fallback for dev; Redis for production multi-replica deployments.
"""

import json
import logging
import time
from abc import ABC, abstractmethod
from collections import OrderedDict, deque

logger = logging.getLogger(__name__)

_MAX_S2S_CACHE = 200  # LRU eviction threshold for in-memory S2S token cache


class DistributedCache(ABC):
    """Abstract base class for distributed cache backends."""

    @abstractmethod
    async def get_s2s_token(self, cache_key: str) -> tuple[str, float] | None:
        """Return (token, expires_at) or None."""

    @abstractmethod
    async def put_s2s_token(self, cache_key: str, token: str, expires_at: float) -> None:
        """Store token with expiry."""

    @abstractmethod
    async def check_rate_limit(self, key: str, limit: int, window_seconds: int) -> bool:
        """Return True if under limit (and increment), False if rate-limited."""

    @abstractmethod
    async def cleanup(self) -> None:
        """Clean up expired entries."""


class InMemoryCache(DistributedCache):
    """In-memory cache for single-process / dev usage."""

    def __init__(self) -> None:
        # S2S tokens: OrderedDict with LRU eviction
        self._s2s_tokens: OrderedDict[str, tuple[str, float]] = OrderedDict()
        # Rate limiting: key -> deque of timestamps
        self._rate_limits: dict[str, deque[float]] = {}

    async def get_s2s_token(self, cache_key: str) -> tuple[str, float] | None:
        entry = self._s2s_tokens.get(cache_key)
        if entry is None:
            return None
        token, expires_at = entry
        if time.time() >= expires_at:
            # Expired — remove it
            self._s2s_tokens.pop(cache_key, None)
            return None
        # Move to end (most recently used)
        self._s2s_tokens.move_to_end(cache_key)
        return token, expires_at

    async def put_s2s_token(self, cache_key: str, token: str, expires_at: float) -> None:
        # Remove existing to update position
        self._s2s_tokens.pop(cache_key, None)
        # LRU eviction if at capacity
        while len(self._s2s_tokens) >= _MAX_S2S_CACHE:
            self._s2s_tokens.popitem(last=False)
        self._s2s_tokens[cache_key] = (token, expires_at)

    async def check_rate_limit(self, key: str, limit: int, window_seconds: int) -> bool:
        now = time.time()
        cutoff = now - window_seconds
        timestamps = self._rate_limits.get(key)
        if timestamps is None:
            timestamps = deque()
            self._rate_limits[key] = timestamps

        # Remove expired entries
        while timestamps and timestamps[0] < cutoff:
            timestamps.popleft()

        if len(timestamps) >= limit:
            return False  # rate-limited

        timestamps.append(now)
        return True  # under limit

    async def cleanup(self) -> None:
        now = time.time()
        # Clean expired S2S tokens
        expired_tokens = [k for k, (_, exp) in self._s2s_tokens.items() if now >= exp]
        for k in expired_tokens:
            self._s2s_tokens.pop(k, None)
        # Clean stale rate limit entries (empty deques)
        empty_keys = [k for k, v in self._rate_limits.items() if not v]
        for k in empty_keys:
            self._rate_limits.pop(k, None)


class RedisCache(DistributedCache):
    """Redis-backed distributed cache for multi-replica production."""

    def __init__(self, redis_client) -> None:
        self._redis = redis_client

    async def get_s2s_token(self, cache_key: str) -> tuple[str, float] | None:
        redis_key = f"asibot:s2s:{cache_key}"
        try:
            raw = self._redis.get(redis_key)
            if raw is None:
                return None
            data = json.loads(raw)
            token = data["token"]
            expires_at = data["expires_at"]
            if time.time() >= expires_at:
                self._redis.delete(redis_key)
                return None
            return token, expires_at
        except Exception:
            logger.warning("Redis get_s2s_token failed for %s, returning None", cache_key, exc_info=True)
            return None

    async def put_s2s_token(self, cache_key: str, token: str, expires_at: float) -> None:
        redis_key = f"asibot:s2s:{cache_key}"
        ttl = max(1, int(expires_at - time.time()))
        payload = json.dumps({"token": token, "expires_at": expires_at})
        try:
            self._redis.setex(redis_key, ttl, payload)
        except Exception:
            logger.warning("Redis put_s2s_token failed for %s", cache_key, exc_info=True)

    async def check_rate_limit(self, key: str, limit: int, window_seconds: int) -> bool:
        # Bucket-based rate limiting: INCR + EXPIRE
        bucket = int(time.time() / window_seconds)
        redis_key = f"asibot:rate:{key}:{bucket}"
        try:
            count = self._redis.incr(redis_key)
            if count == 1:
                # First request in this window — set TTL
                self._redis.expire(redis_key, window_seconds)
            return count <= limit
        except Exception:
            # On Redis failure, allow the request (fail open)
            logger.warning("Redis check_rate_limit failed for %s, allowing request", key, exc_info=True)
            return True

    async def cleanup(self) -> None:
        # Redis handles TTL-based expiry automatically — nothing to do
        pass


async def create_distributed_cache() -> DistributedCache:
    """Factory: create the appropriate cache backend based on config."""
    from asibot.config import settings

    backend = getattr(settings, "session_backend", "memory")
    redis_url = getattr(settings, "redis_url", "")

    if backend == "redis" and redis_url:
        try:
            import redis

            client = redis.Redis.from_url(redis_url, decode_responses=True)
            # Verify connectivity
            client.ping()
            logger.info("Distributed cache: Redis (%s)", redis_url)
            return RedisCache(client)
        except ImportError:
            logger.warning("redis package not installed, falling back to in-memory cache")
        except Exception:
            logger.warning("Redis connection failed, falling back to in-memory cache", exc_info=True)

    logger.info("Distributed cache: in-memory")
    return InMemoryCache()


# --- Module-level singleton ---

_cache: DistributedCache | None = None


async def init_cache() -> DistributedCache:
    """Initialize the global distributed cache singleton."""
    global _cache
    _cache = await create_distributed_cache()
    return _cache


def get_cache() -> DistributedCache:
    """Get the global distributed cache. Raises RuntimeError if not initialized."""
    if _cache is None:
        raise RuntimeError("Distributed cache not initialized. Call init_cache() first.")
    return _cache
