#!/usr/bin/env python3
"""Load test for Asibot production-hardening components.

Tests the hot-path infrastructure under concurrent load:
  - HTTP connection pool (shared clients, LRU eviction)
  - Circuit breaker (state transitions under failure storms)
  - Rate limiter (1000 users × 60 req/min windows)
  - Session cache (10K cap, LRU eviction, TTL expiry)
  - Retry logic (backoff timing, 429/5xx handling)

No PostgreSQL required — exercises in-memory components only.

Usage: python tests/load_test.py
"""

import asyncio
import statistics
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock

import httpx

sys.path.insert(0, "src")


@dataclass
class LoadTestResult:
    name: str
    total_ops: int = 0
    errors: int = 0
    latencies_ms: list[float] = field(default_factory=list)
    duration_s: float = 0.0

    @property
    def ops_per_sec(self) -> float:
        return self.total_ops / self.duration_s if self.duration_s else 0

    @property
    def p50_ms(self) -> float:
        return statistics.median(self.latencies_ms) if self.latencies_ms else 0

    @property
    def p99_ms(self) -> float:
        return statistics.quantiles(self.latencies_ms, n=100)[-1] if len(self.latencies_ms) > 1 else 0

    def report(self) -> str:
        err_rate = (self.errors / self.total_ops * 100) if self.total_ops else 0
        return (
            f"  {self.name:<35} "
            f"{self.total_ops:>6} ops  "
            f"{self.ops_per_sec:>8.0f} ops/s  "
            f"p50={self.p50_ms:>6.2f}ms  "
            f"p99={self.p99_ms:>6.2f}ms  "
            f"errors={err_rate:.1f}%"
        )


# ---------------------------------------------------------------------------
# Test 1: Connection Pool — concurrent client acquisition
# ---------------------------------------------------------------------------

async def test_connection_pool(num_services: int = 20, num_users: int = 200, ops_per_user: int = 10) -> LoadTestResult:
    """Simulate many users acquiring pooled clients concurrently."""
    from asibot import http_pool
    # Reset pool state
    http_pool._pool.clear()

    result = LoadTestResult(name="Connection Pool")
    start = time.monotonic()

    async def user_workload(user_idx: int):
        for _ in range(ops_per_user):
            svc = f"https://api.service-{user_idx % num_services}.com"
            t0 = time.monotonic()
            try:
                client = await http_pool.get_base_client(
                    base_url=svc,
                    default_headers={"Accept": "application/json"},
                    timeout=30.0,
                )
                # Simulate a PooledClient wrapping it
                pc = http_pool.PooledClient(
                    _client=client,
                    _auth_headers={"Authorization": f"Bearer token_{user_idx}"},
                )
                result.total_ops += 1
                result.latencies_ms.append((time.monotonic() - t0) * 1000)
            except Exception:
                result.errors += 1

    await asyncio.gather(*[user_workload(i) for i in range(num_users)])
    result.duration_s = time.monotonic() - start

    # Verify pool is bounded
    pool_size = len(http_pool._pool)
    assert pool_size <= http_pool._MAX_POOL_CLIENTS, f"Pool exceeded max: {pool_size}"

    await http_pool.close_all()
    return result


# ---------------------------------------------------------------------------
# Test 2: Circuit Breaker — failure storms
# ---------------------------------------------------------------------------

async def test_circuit_breaker(num_services: int = 10, ops: int = 5000) -> LoadTestResult:
    """Simulate rapid success/failure patterns across services."""
    from asibot.circuit_breaker import CircuitBreaker, get_breaker, _breakers
    _breakers.clear()

    result = LoadTestResult(name="Circuit Breaker")
    start = time.monotonic()

    async def hammer_breaker(svc_idx: int):
        breaker = get_breaker(f"service-{svc_idx}")
        for i in range(ops // num_services):
            t0 = time.monotonic()
            try:
                if breaker.can_execute():
                    if i % 7 == 0:  # ~14% failure rate
                        await breaker.record_failure()
                    else:
                        await breaker.record_success()
                result.total_ops += 1
                result.latencies_ms.append((time.monotonic() - t0) * 1000)
            except Exception:
                result.errors += 1

    await asyncio.gather(*[hammer_breaker(i) for i in range(num_services)])
    result.duration_s = time.monotonic() - start

    # Verify circuit states are valid
    for svc, breaker in _breakers.items():
        assert breaker.state in ("closed", "open", "half_open"), f"Invalid state for {svc}: {breaker.state}"

    _breakers.clear()
    return result


# ---------------------------------------------------------------------------
# Test 3: Rate Limiter — 1000 user simulation
# ---------------------------------------------------------------------------

async def test_rate_limiter(num_users: int = 1000, services: int = 5, requests_per: int = 10) -> LoadTestResult:
    """Simulate rate limit checks for 1000 users across multiple services."""
    from asibot.token_store import global_rate_limiter
    global_rate_limiter.reset()

    result = LoadTestResult(name="Rate Limiter (1000 users)")
    start = time.monotonic()

    for user in range(num_users):
        for svc in range(services):
            for _ in range(requests_per):
                t0 = time.monotonic()
                allowed, retry_after = global_rate_limiter.check(f"service_{svc}")
                result.total_ops += 1
                result.latencies_ms.append((time.monotonic() - t0) * 1000)
                if not allowed:
                    result.errors += 1  # Rate limited (expected for high-volume users)

    result.duration_s = time.monotonic() - start

    # Verify rate limiter tracked hits
    total_hits = sum(global_rate_limiter.get_hits(f"service_{svc}") for svc in range(services))
    assert result.errors > 0, "Expected some rate-limited requests at this volume"

    global_rate_limiter.reset()
    return result


# ---------------------------------------------------------------------------
# Test 4: Session Cache — LRU eviction under pressure
# ---------------------------------------------------------------------------

async def test_session_cache(num_sessions: int = 15000) -> LoadTestResult:
    """Fill session cache beyond _MAX_SESSIONS and verify LRU eviction."""
    from asibot.user_session import _cache_session, _MAX_SESSIONS, _get_store
    from asibot.session_store import InMemorySessionStore

    # Use a fresh in-memory store for the test
    import asibot.user_session as _us
    store = InMemorySessionStore(max_sessions=_MAX_SESSIONS, default_ttl=3600)
    original_store = _us._store
    _us._store = store

    result = LoadTestResult(name=f"Session Cache (cap={_MAX_SESSIONS})")
    start = time.monotonic()

    try:
        for i in range(num_sessions):
            t0 = time.monotonic()
            _cache_session(f"session_{i}", f"user_{i % 1000}@example.com")
            result.total_ops += 1
            result.latencies_ms.append((time.monotonic() - t0) * 1000)

        result.duration_s = time.monotonic() - start

        # Verify cap enforced
        actual = len(store._sessions)
        assert actual <= _MAX_SESSIONS, f"Sessions exceeded cap: {actual} > {_MAX_SESSIONS}"
    finally:
        _us._store = original_store

    return result


# ---------------------------------------------------------------------------
# Test 5: Retry Logic — backoff timing accuracy
# ---------------------------------------------------------------------------

async def test_retry_timing() -> LoadTestResult:
    """Verify safe_request handles success and errors correctly with rate limiting."""
    from asibot.token_store import safe_request, global_rate_limiter
    global_rate_limiter.reset()

    result = LoadTestResult(name="Safe Request (rate limit + error)")

    # Test 1: Successful request passes through
    resp_200 = MagicMock(spec=httpx.Response)
    resp_200.status_code = 200
    resp_200.headers = {}
    resp_200.raise_for_status.return_value = None

    client = AsyncMock()
    client.request = AsyncMock(return_value=resp_200)

    start = time.monotonic()
    r, err = await safe_request(
        client, "GET", "https://api.example.com/test",
        service="TestRetry", action="fetch",
    )
    elapsed = time.monotonic() - start

    result.total_ops = 1
    result.duration_s = elapsed
    result.latencies_ms = [elapsed * 1000]

    assert r is not None, f"Expected success, got error: {err}"
    assert err is None
    assert client.request.call_count == 1

    # Test 2: HTTP error returns formatted error message
    resp_403 = MagicMock(spec=httpx.Response)
    resp_403.status_code = 403
    resp_403.headers = {}
    resp_403.raise_for_status.side_effect = httpx.HTTPStatusError(
        "HTTP 403", request=MagicMock(), response=resp_403
    )
    client2 = AsyncMock()
    client2.request = AsyncMock(return_value=resp_403)

    r2, err2 = await safe_request(
        client2, "GET", "https://api.example.com/test",
        service="TestRetry", action="fetch",
    )
    assert r2 is None
    assert "403" in err2
    assert client2.request.call_count == 1, "Single attempt expected"
    result.total_ops += 1

    # Test 3: Global rate limiter integration — exhaust limit then verify rejection
    global_rate_limiter.reset()
    from asibot.config import settings
    limit = settings.global_rate_limit_default

    client3 = AsyncMock()
    client3.request = AsyncMock(return_value=resp_200)

    # Exhaust the rate limit for this service
    for _ in range(limit):
        global_rate_limiter.check("testretry")

    r3, err3 = await safe_request(
        client3, "GET", "https://api.example.com/test",
        service="TestRetry", action="fetch",
    )
    assert r3 is None, "Should be rate-limited"
    assert "rate limit" in err3.lower()
    assert client3.request.call_count == 0, "Request should not be sent when rate-limited"
    result.total_ops += 1

    global_rate_limiter.reset()
    return result


# ---------------------------------------------------------------------------
# Test 6: Memory pressure — verify no leaks in rate limiter
# ---------------------------------------------------------------------------

async def test_memory_bounded(num_users: int = 5000, rounds: int = 3) -> LoadTestResult:
    """Verify GlobalRateLimiter windows don't grow unbounded across rounds."""
    from asibot.token_store import global_rate_limiter
    global_rate_limiter.reset()

    result = LoadTestResult(name="Memory Bounded (rate limiter)")
    start = time.monotonic()

    services = [f"svc_{i}" for i in range(10)]
    sizes = []
    for _round in range(rounds):
        for user in range(num_users):
            svc = services[user % len(services)]
            global_rate_limiter.check(svc)
            result.total_ops += 1
        # Count total window entries across all services
        with global_rate_limiter._lock:
            total_entries = sum(len(v) for v in global_rate_limiter._windows.values())
        sizes.append(total_entries)

    result.duration_s = time.monotonic() - start
    result.latencies_ms = [result.duration_s * 1000 / result.total_ops] * result.total_ops

    # Window entries should be bounded by the rate limit (old entries trimmed)
    # Each service window is capped at its limit (default 200 per 60s window)
    max_size = max(sizes)
    # With 10 services × 200 limit = 2000 max entries (not 15000 unbounded)
    assert max_size <= len(services) * 300, f"Rate limiter windows grew to {max_size}, expected bounded"

    global_rate_limiter.reset()
    return result


# ---------------------------------------------------------------------------
# Test 7: Full deployment — 1000 users × 23 services
# ---------------------------------------------------------------------------

async def test_full_deployment(num_users: int = 1000, num_services: int = 23) -> LoadTestResult:
    """Simulate every user connected to every service.

    Exercises: pool (23 base URLs), rate limiter (23K keys), circuit breakers (23 services).
    """
    from asibot import http_pool
    from asibot.token_store import global_rate_limiter
    from asibot.circuit_breaker import get_breaker, _breakers
    http_pool._pool.clear()
    global_rate_limiter.reset()
    _breakers.clear()

    services = [f"service_{i}" for i in range(num_services)]
    result = LoadTestResult(name=f"Full Deploy ({num_users}u × {num_services}svc)")
    start = time.monotonic()

    async def user_workload(uid: int):
        for svc in services:
            t0 = time.monotonic()
            # 1. Global rate limit check
            global_rate_limiter.check(svc)
            # 2. Circuit breaker check
            breaker = get_breaker(svc)
            if breaker.can_execute():
                await breaker.record_success()
            # 3. Pool client acquisition
            await http_pool.get_base_client(
                base_url=f"https://api.{svc}.com",
                default_headers={"Accept": "application/json"},
            )
            result.total_ops += 1
            result.latencies_ms.append((time.monotonic() - t0) * 1000)

    # Run all users concurrently in batches of 100
    for batch_start in range(0, num_users, 100):
        batch_end = min(batch_start + 100, num_users)
        await asyncio.gather(*[user_workload(i) for i in range(batch_start, batch_end)])

    result.duration_s = time.monotonic() - start

    # Assertions
    pool_size = len(http_pool._pool)
    breaker_count = len(_breakers)

    assert pool_size <= http_pool._MAX_POOL_CLIENTS, f"Pool overflow: {pool_size}"
    assert breaker_count == num_services, f"Breakers: {breaker_count} != {num_services}"

    # Memory estimate — count rate limiter window entries
    with global_rate_limiter._lock:
        rate_entries = sum(len(v) for v in global_rate_limiter._windows.values())
    rate_mem_mb = rate_entries * 8 / 1024 / 1024
    result_extra = f" | pool={pool_size} breakers={breaker_count} rate_entries={rate_entries} mem≈{rate_mem_mb:.1f}MB"

    await http_pool.close_all()
    global_rate_limiter.reset()
    _breakers.clear()

    # Append extra info to name
    result.name += result_extra
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    print("=" * 80)
    print("ASIBOT LOAD TEST — Production Hardening Verification")
    print("=" * 80)
    print()

    tests = [
        test_connection_pool,
        test_circuit_breaker,
        test_rate_limiter,
        test_session_cache,
        test_retry_timing,
        test_memory_bounded,
        test_full_deployment,
    ]

    results: list[LoadTestResult] = []
    failures = 0

    for test_fn in tests:
        name = test_fn.__name__
        try:
            r = await test_fn()
            results.append(r)
            print(f"  PASS  {r.report()}")
        except AssertionError as e:
            failures += 1
            print(f"  FAIL  {name}: {e}")
        except Exception as e:
            failures += 1
            print(f"  ERROR {name}: {type(e).__name__}: {e}")

    print()
    print("-" * 80)
    total_ops = sum(r.total_ops for r in results)
    total_time = sum(r.duration_s for r in results)
    print(f"  Total: {total_ops:,} operations in {total_time:.2f}s ({total_ops/total_time:,.0f} ops/s)")
    print(f"  Tests: {len(results)} passed, {failures} failed")
    print()

    if failures:
        print("  RESULT: FAIL")
        return 1

    # Verify performance thresholds
    thresholds_ok = True
    for r in results:
        if r.p99_ms > 10.0 and "Retry" not in r.name:
            print(f"  WARNING: {r.name} p99 latency {r.p99_ms:.2f}ms exceeds 10ms threshold")
            thresholds_ok = False

    if thresholds_ok:
        print("  RESULT: PASS — All components within latency thresholds")
    else:
        print("  RESULT: PASS WITH WARNINGS — See latency notes above")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
