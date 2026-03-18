"""Shared HTTP connection pool for connector clients.

Instead of creating a fresh httpx.AsyncClient per tool call, this module
maintains a pool of shared clients keyed by (base_url, default_headers).
Per-user auth is injected per-request via PooledClient wrappers.

This reduces TCP connection churn from 10K connections/sec (at 1000 users)
down to a handful of persistent pooled connections per service.
"""

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_MAX_POOL_CLIENTS = 200
_IDLE_TIMEOUT = 300  # 5 minutes — close clients not used recently

# Pool of shared base clients keyed by (base_url or service_name)
_pool: OrderedDict[str, tuple[httpx.AsyncClient, float]] = OrderedDict()
_pool_lock = asyncio.Lock()


def _pool_key(base_url: str | None, headers_frozen: tuple) -> str:
    """Derive a cache key from base_url and static headers."""
    return f"{base_url or ''}|{hash(headers_frozen)}"


async def get_base_client(
    *,
    base_url: str | None = None,
    default_headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> httpx.AsyncClient:
    """Get or create a shared base client for a service.

    The returned client has connection pooling but NO auth headers.
    Auth must be applied per-request via PooledClient.
    """
    headers_frozen = tuple(sorted((default_headers or {}).items()))
    key = _pool_key(base_url, headers_frozen)

    async with _pool_lock:
        if key in _pool:
            client, _ = _pool[key]
            _pool[key] = (client, time.time())
            _pool.move_to_end(key)
            return client

        # Evict oldest if at capacity
        while len(_pool) >= _MAX_POOL_CLIENTS:
            evict_key, (evict_client, _) = _pool.popitem(last=False)
            try:
                await evict_client.aclose()
            except Exception:
                logger.debug("Failed to close evicted pool client for %s", evict_key)

        kwargs: dict[str, Any] = {
            "timeout": timeout,
            "limits": httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
            ),
        }
        if base_url:
            kwargs["base_url"] = base_url
        if default_headers:
            kwargs["headers"] = default_headers

        client = httpx.AsyncClient(**kwargs)
        _pool[key] = (client, time.time())
        logger.debug("Created pool client: %s", key[:80])
        return client


@dataclass
class PooledClient:
    """Wraps a shared httpx.AsyncClient with per-request auth injection.

    Quacks like httpx.AsyncClient for the subset of API used by safe_request()
    (only .request() is called). Auth headers/credentials are applied on every
    request without modifying the shared underlying client.
    """

    _client: httpx.AsyncClient
    _auth_headers: dict[str, str] = field(default_factory=dict)
    _auth: tuple[str, str] | None = None  # For basic auth

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        # Merge auth headers into request headers
        if self._auth_headers:
            req_headers = dict(kwargs.pop("headers", {}) or {})
            # Auth headers from pool; request-specific headers win on conflict
            merged = {**self._auth_headers, **req_headers}
            kwargs["headers"] = merged

        if self._auth is not None:
            kwargs["auth"] = self._auth

        return await self._client.request(method, url, **kwargs)


async def close_all() -> None:
    """Close all pooled clients. Call on server shutdown."""
    async with _pool_lock:
        for key, (client, _) in list(_pool.items()):
            try:
                await client.aclose()
            except Exception:
                logger.debug("Failed to close pool client: %s", key[:80])
        _pool.clear()
    logger.info("HTTP pool: closed all clients")


async def cleanup_idle() -> None:
    """Close pool clients that haven't been used recently."""
    cutoff = time.time() - _IDLE_TIMEOUT
    async with _pool_lock:
        to_evict = [k for k, (_, ts) in _pool.items() if ts < cutoff]
        for key in to_evict:
            client, _ = _pool.pop(key)
            try:
                await client.aclose()
            except Exception:
                logger.debug("Failed to close idle pool client: %s", key[:80])
        if to_evict:
            logger.info("HTTP pool: closed %d idle clients", len(to_evict))


def pool_stats() -> dict:
    """Return pool statistics for health checks and metrics."""
    return {
        "pool_size": len(_pool),
        "max_pool_size": _MAX_POOL_CLIENTS,
    }
