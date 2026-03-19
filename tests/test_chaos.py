"""Chaos engineering / failure injection tests for the Asibot MCP server.

Tests cover:
- Circuit breaker state transitions (closed -> open -> half-open -> closed)
- Retry exhaustion and backoff behaviour in safe_request()
- Audit resilience (primary failure -> JSONL fallback, dual-write failure)
- Session cache under pressure (max capacity, concurrent ops, eviction)
- Connection pool exhaustion (mocked asyncpg pool with small capacity)
"""

import asyncio
import logging
import time
from collections import OrderedDict
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from asibot.circuit_breaker import CircuitBreaker, get_breaker, _breakers


# ===================================================================
# Fixtures
# ===================================================================


@pytest.fixture(autouse=True)
def _clean_breakers():
    """Ensure global breaker registry is clean before/after each test."""
    saved = dict(_breakers)
    _breakers.clear()
    yield
    _breakers.clear()
    _breakers.update(saved)


# ===================================================================
# 1. Circuit Breaker Tests
# ===================================================================


class TestCircuitBreakerStateTransitions:
    """Verify the full lifecycle: CLOSED -> OPEN -> HALF_OPEN -> CLOSED."""

    @pytest.mark.asyncio
    async def test_breaker_opens_after_n_consecutive_failures(self):
        """Breaker must transition to OPEN after failure_threshold failures."""
        breaker = CircuitBreaker("test-svc", failure_threshold=3, recovery_timeout=60)
        assert breaker.state == "closed"
        assert breaker.can_execute() is True

        for _ in range(3):
            await breaker.record_failure()

        assert breaker.state == "open"

    @pytest.mark.asyncio
    async def test_breaker_rejects_when_open(self):
        """OPEN breaker must fast-fail (return False from can_execute)."""
        breaker = CircuitBreaker("test-svc", failure_threshold=2, recovery_timeout=300)

        await breaker.record_failure()
        await breaker.record_failure()
        assert breaker.state == "open"
        assert breaker.can_execute() is False
        assert breaker.time_until_recovery > 0

    @pytest.mark.asyncio
    async def test_breaker_transitions_to_half_open_after_recovery_timeout(self):
        """After recovery_timeout elapses, state should appear as half_open."""
        breaker = CircuitBreaker("test-svc", failure_threshold=1, recovery_timeout=0.05)

        await breaker.record_failure()
        assert breaker.state == "open"

        # Wait just past the recovery timeout
        await asyncio.sleep(0.06)

        assert breaker.state == "half_open"
        assert breaker.can_execute() is True  # probe allowed

    @pytest.mark.asyncio
    async def test_breaker_closes_after_success_in_half_open(self):
        """A successful request in HALF_OPEN state should close the breaker."""
        breaker = CircuitBreaker("test-svc", failure_threshold=1, recovery_timeout=0.05)

        await breaker.record_failure()
        assert breaker.state == "open"

        await asyncio.sleep(0.06)
        assert breaker.state == "half_open"

        # Allow probe
        assert breaker.can_execute() is True

        # Successful probe -> close
        await breaker.record_success()
        assert breaker.state == "closed"
        assert breaker.can_execute() is True

    @pytest.mark.asyncio
    async def test_breaker_reopens_on_failure_in_half_open(self):
        """A failed probe in HALF_OPEN should reopen the circuit."""
        breaker = CircuitBreaker("test-svc", failure_threshold=1, recovery_timeout=0.05)

        await breaker.record_failure()
        await asyncio.sleep(0.06)

        assert breaker.can_execute() is True  # first probe in half_open
        await breaker.record_failure()  # probe fails

        assert breaker.state == "open"
        assert breaker.can_execute() is False

    @pytest.mark.asyncio
    async def test_success_resets_consecutive_failure_count(self):
        """A success before threshold resets the failure counter."""
        breaker = CircuitBreaker("test-svc", failure_threshold=3)

        await breaker.record_failure()
        await breaker.record_failure()
        await breaker.record_success()  # reset counter
        await breaker.record_failure()

        # Should still be closed (1 failure after reset, not 3)
        assert breaker.state == "closed"

    @pytest.mark.asyncio
    async def test_half_open_limits_probe_count(self):
        """In HALF_OPEN, only half_open_max probes should be allowed."""
        breaker = CircuitBreaker(
            "test-svc", failure_threshold=1, recovery_timeout=0.05, half_open_max=1
        )
        await breaker.record_failure()
        await asyncio.sleep(0.06)

        assert breaker.can_execute() is True   # probe 1 allowed
        assert breaker.can_execute() is False  # probe 2 blocked

    @pytest.mark.asyncio
    async def test_concurrent_requests_during_state_transitions(self):
        """Multiple concurrent record_failure calls should not corrupt state."""
        breaker = CircuitBreaker("test-svc", failure_threshold=5)

        # Fire 10 concurrent failures — breaker should open exactly once
        tasks = [breaker.record_failure() for _ in range(10)]
        await asyncio.gather(*tasks)

        assert breaker.state == "open"
        assert breaker._consecutive_failures == 10

    @pytest.mark.asyncio
    async def test_manual_reset(self):
        """reset() should bring the breaker back to closed from any state."""
        breaker = CircuitBreaker("test-svc", failure_threshold=1)
        await breaker.record_failure()
        assert breaker.state == "open"

        await breaker.reset()
        assert breaker.state == "closed"
        assert breaker.can_execute() is True

    def test_get_breaker_returns_singleton(self):
        """get_breaker() should return the same instance for the same service."""
        b1 = get_breaker("singleton-test")
        b2 = get_breaker("singleton-test")
        assert b1 is b2


# ===================================================================
# 2. Retry Exhaustion Tests (safe_request)
# ===================================================================


def _make_http_error(status_code: int, headers: dict | None = None) -> httpx.HTTPStatusError:
    """Build a mock HTTPStatusError with the given status code and headers."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.headers = headers or {}
    return httpx.HTTPStatusError(
        f"HTTP {status_code}", request=MagicMock(), response=resp
    )


class TestSafeRequest:
    """Tests for safe_request — single-attempt HTTP helper with error formatting."""

    @pytest.mark.asyncio
    async def test_http_error_returns_formatted_error(self):
        """HTTPStatusError should be caught and formatted as an error string."""
        from asibot.token_store import safe_request

        client = AsyncMock()
        client.request.side_effect = _make_http_error(503)

        resp, err = await safe_request(
            client, "GET", "https://api.example.com/test",
            service="TestSvc", action="fetch",
            max_retries=0,
        )

        assert resp is None
        assert "TestSvc fetch failed" in err
        assert "HTTP 503" in err
        # safe_request with max_retries=0 makes exactly one attempt
        assert client.request.call_count == 1

    @pytest.mark.asyncio
    async def test_429_returns_error(self):
        """429 Too Many Requests should be returned as an error."""
        from asibot.token_store import safe_request

        client = AsyncMock()
        client.request.side_effect = _make_http_error(429, {"Retry-After": "0.01"})

        resp, err = await safe_request(
            client, "GET", "/test",
            service="TestSvc", action="fetch",
        )

        assert resp is None
        assert err is not None
        assert "HTTP 429" in err

    @pytest.mark.asyncio
    async def test_successful_request_returns_response(self):
        """A successful request should return (response, None)."""
        from asibot.token_store import safe_request

        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        client = AsyncMock()
        client.request.return_value = mock_resp

        resp, err = await safe_request(
            client, "GET", "/test",
            service="TestSvc", action="fetch",
        )

        assert resp is mock_resp
        assert err is None

    @pytest.mark.asyncio
    async def test_4xx_errors_return_formatted_error(self):
        """4xx errors should be caught and formatted."""
        from asibot.token_store import safe_request

        for status in (400, 401, 403, 404):
            client = AsyncMock()
            client.request.side_effect = _make_http_error(status)

            resp, err = await safe_request(
                client, "GET", "/test",
                service="TestSvc", action="fetch",
            )

            assert resp is None
            assert f"HTTP {status}" in err
            # Exactly one attempt
            assert client.request.call_count == 1, (
                f"Status {status} should only trigger one call"
            )

    @pytest.mark.asyncio
    async def test_network_error_returns_formatted_error(self):
        """Network errors (RequestError) should be caught and formatted."""
        from asibot.token_store import safe_request

        client = AsyncMock()
        client.request.side_effect = httpx.ConnectError("Connection refused")

        resp, err = await safe_request(
            client, "GET", "/test",
            service="TestSvc", action="connect",
        )

        assert resp is None
        assert "network error" in err

    @pytest.mark.asyncio
    async def test_value_error_returns_formatted_error(self):
        """ValueError during request should be caught and formatted."""
        from asibot.token_store import safe_request

        client = AsyncMock()
        client.request.side_effect = ValueError("bad JSON")

        resp, err = await safe_request(
            client, "GET", "/test",
            service="TestSvc", action="parse",
        )

        assert resp is None
        assert "bad JSON" in err
        # Should be called exactly once
        assert client.request.call_count == 1

    @pytest.mark.asyncio
    async def test_rate_limited_returns_error_without_http_call(self):
        """When circuit breaker is open, no HTTP call should be made."""
        from asibot.token_store import safe_request
        from asibot.circuit_breaker import CircuitBreaker

        client = AsyncMock()

        # Create a breaker that is already open (rejects all requests)
        mock_breaker = CircuitBreaker("TestSvc", failure_threshold=1, recovery_timeout=300)
        await mock_breaker.record_failure()  # Open the circuit
        assert mock_breaker.state == "open"

        with patch("asibot.token_store.get_breaker", return_value=mock_breaker):
            resp, err = await safe_request(
                client, "GET", "/test",
                service="TestSvc", action="fetch",
            )

        assert resp is None
        assert "circuit breaker" in err.lower()
        assert client.request.call_count == 0


# ===================================================================
# 3. Audit Resilience Tests
# ===================================================================


class TestAuditResilience:

    def test_primary_failure_falls_back_to_jsonl(self, tmp_path):
        """When primary audit logger fails, entry should be written to JSONL fallback."""
        from asibot import audit

        with patch.object(audit.settings, "data_dir", tmp_path):
            # Reset the cached audit logger so our patched data_dir takes effect
            audit._audit_logger = None

            # Make the primary logger always raise
            mock_logger = MagicMock()
            mock_logger.info.side_effect = OSError("disk full")
            mock_logger.setLevel = MagicMock()
            mock_logger.propagate = False
            mock_logger.addHandler = MagicMock()

            with patch.object(audit, "_get_audit_logger", return_value=mock_logger):
                with patch("asibot.audit.time.sleep"):  # skip the 1s retry delay
                    audit.log_tool_call("user@test.com", "github_search", {"query": "test"})

            fallback = tmp_path / "audit_fallback.jsonl"
            assert fallback.exists()
            content = fallback.read_text()
            assert "github_search" in content
            assert "user@test.com" in content

    def test_both_primary_and_fallback_failure_logs_critical(self, tmp_path):
        """When both primary and JSONL fallback fail, CRITICAL should be logged."""
        from asibot import audit

        audit.audit_write_failures_total = 0
        critical_messages: list[str] = []
        original_critical = audit.logger.critical

        def capture_critical(msg, *args):
            critical_messages.append(msg % args if args else msg)

        with patch.object(audit.settings, "data_dir", tmp_path):
            audit._audit_logger = None

            mock_logger = MagicMock()
            mock_logger.info.side_effect = OSError("disk full")

            with (
                patch.object(audit, "_get_audit_logger", return_value=mock_logger),
                patch.object(audit, "_write_jsonl_fallback", side_effect=OSError("fallback also full")),
                patch("asibot.audit.time.sleep"),
                patch.object(audit.logger, "critical", side_effect=capture_critical),
            ):
                audit.log_tool_call("user@test.com", "important_action")

            assert any("AUDIT LOSS" in m for m in critical_messages)

    def test_audit_does_not_block_on_failure(self, tmp_path):
        """Audit failures should not raise exceptions to the caller."""
        from asibot import audit

        with patch.object(audit.settings, "data_dir", tmp_path):
            audit._audit_logger = None

            mock_logger = MagicMock()
            mock_logger.info.side_effect = OSError("disk full")

            with (
                patch.object(audit, "_get_audit_logger", return_value=mock_logger),
                patch.object(audit, "_write_jsonl_fallback", side_effect=OSError("all full")),
                patch("asibot.audit.time.sleep"),
            ):
                # This must not raise -- audit should never block tool execution
                audit.log_tool_call("user@test.com", "some_tool", {"key": "value"})
                audit.log_tool_call("user@test.com", "another_tool")

    def test_audit_redacts_secrets(self):
        """Credential-like keys should be replaced with '***' in audit args."""
        from asibot.audit import _redact_args

        args = {
            "query": "visible",
            "token": "ghp_secret",
            "api_key": "sk_secret",
            "client_secret": "cs_secret",
            "password": "pw123",
            "normal_field": "stays",
        }
        safe = _redact_args(args)
        assert safe["query"] == "visible"
        assert safe["normal_field"] == "stays"
        assert safe["token"] == "***"
        assert safe["api_key"] == "***"
        assert safe["client_secret"] == "***"
        assert safe["password"] == "***"


# ===================================================================
# 4. Session Chaos Tests
# ===================================================================


class TestSessionChaos:

    def test_session_cache_at_max_capacity_evicts_oldest(self):
        """Adding a session at max capacity should evict the oldest (LRU)."""
        from asibot import user_session
        from asibot.session_store import InMemorySessionStore

        max_sessions = user_session._MAX_SESSIONS
        store = InMemorySessionStore(max_sessions=max_sessions, default_ttl=3600)
        original_store = user_session._store
        try:
            user_session._store = store

            # Fill to _MAX_SESSIONS
            for i in range(max_sessions):
                user_session._cache_session(f"sid-{i}", f"user-{i}@test.com")

            assert len(store._sessions) == max_sessions

            # Verify oldest entry exists
            assert "sid-0" in store._sessions

            # Add one more -- should evict sid-0 (oldest)
            user_session._cache_session("sid-new", "new-user@test.com")

            assert len(store._sessions) == max_sessions
            assert "sid-new" in store._sessions
            assert "sid-0" not in store._sessions
        finally:
            user_session._store = original_store

    def test_session_cache_lru_order_maintained(self):
        """Accessing a session should move it to the end (LRU)."""
        from asibot import user_session
        from asibot.session_store import InMemorySessionStore

        store = InMemorySessionStore(max_sessions=10_000, default_ttl=3600)
        original_store = user_session._store
        try:
            user_session._store = store

            user_session._cache_session("sid-A", "userA@test.com")
            user_session._cache_session("sid-B", "userB@test.com")
            user_session._cache_session("sid-C", "userC@test.com")

            # Access A (moves to end)
            user_session._cache_session("sid-A", "userA@test.com")

            # Order should now be B, C, A
            keys = list(store._sessions.keys())
            assert keys == ["sid-B", "sid-C", "sid-A"]
        finally:
            user_session._store = original_store

    @pytest.mark.asyncio
    async def test_concurrent_session_creation_and_invalidation(self):
        """Concurrent session creation + invalidation should not corrupt state."""
        from asibot import user_session
        from asibot.session_store import InMemorySessionStore

        store = InMemorySessionStore(max_sessions=10_000, default_ttl=3600)
        original_store = user_session._store
        try:
            user_session._store = store

            async def create_sessions(start: int, count: int):
                for i in range(start, start + count):
                    user_session._cache_session(f"sid-{i}", "victim@test.com")

            async def invalidate():
                await asyncio.sleep(0)  # yield to let creation start
                user_session.invalidate_user_sessions("victim@test.com")

            # Run creation and invalidation concurrently
            await asyncio.gather(
                create_sessions(0, 50),
                invalidate(),
                create_sessions(50, 50),
            )

            # State must be consistent -- no crashes, no duplicate keys
            assert len(store._sessions) <= 100
            # All surviving entries should have valid structure
            for sid, (uid, ts) in store._sessions.items():
                assert isinstance(uid, str)
                assert isinstance(ts, float)
        finally:
            user_session._store = original_store

    def test_session_lookup_during_eviction(self):
        """Evicting stale sessions should not interfere with valid lookups."""
        from asibot import user_session
        from asibot.session_store import InMemorySessionStore

        store = InMemorySessionStore(max_sessions=10_000, default_ttl=3600)
        original_store = user_session._store
        try:
            user_session._store = store

            # Add expired sessions (timestamp far in the past)
            expired_ts = time.time() - store._default_ttl - 100
            for i in range(100):
                store._sessions[f"expired-{i}"] = (
                    f"user-{i}@test.com", expired_ts
                )

            # Add a valid session
            user_session._cache_session("valid-sid", "valid@test.com")

            # Eviction should remove the expired ones
            store.evict_expired()

            # Valid session must survive
            assert "valid-sid" in store._sessions
            uid, ts = store._sessions["valid-sid"]
            assert uid == "valid@test.com"
            # Expired sessions should be gone
            assert "expired-0" not in store._sessions
        finally:
            user_session._store = original_store

    def test_invalidate_nonexistent_user_is_noop(self):
        """Invalidating a user with no sessions should return 0 and not crash."""
        from asibot import user_session
        from asibot.session_store import InMemorySessionStore

        store = InMemorySessionStore(max_sessions=10_000, default_ttl=3600)
        original_store = user_session._store
        try:
            user_session._store = store
            user_session._cache_session("sid-1", "other@test.com")

            count = user_session.invalidate_user_sessions("nobody@test.com")
            assert count == 0
            assert len(store._sessions) == 1
        finally:
            user_session._store = original_store


# ===================================================================
# 5. Connection Pool Exhaustion Tests
# ===================================================================


class TestConnectionPoolExhaustion:

    @pytest.mark.asyncio
    async def test_many_concurrent_db_operations_with_small_pool(self):
        """Simulate many concurrent DB calls with a pool that can only hold 2 connections."""
        from asibot.db_postgres import PostgresBackend

        # Create a mock pool that tracks acquire/release
        acquired = 0
        max_concurrent = 0
        pool_size = 2

        class FakeConnection:
            async def execute(self, *args, **kwargs):
                return "UPDATE 1"

            async def fetch(self, *args, **kwargs):
                return []

            async def fetchrow(self, *args, **kwargs):
                return None

        class FakePool:
            """Simulates a pool with limited concurrency."""
            def __init__(self):
                self._semaphore = asyncio.Semaphore(pool_size)
                self._acquired = 0
                self._max_concurrent = 0
                self._lock = asyncio.Lock()

            class _AcquireContext:
                def __init__(self, pool_obj):
                    self._pool = pool_obj

                async def __aenter__(self):
                    await self._pool._semaphore.acquire()
                    async with self._pool._lock:
                        self._pool._acquired += 1
                        self._pool._max_concurrent = max(
                            self._pool._max_concurrent, self._pool._acquired
                        )
                    return FakeConnection()

                async def __aexit__(self, *args):
                    async with self._pool._lock:
                        self._pool._acquired -= 1
                    self._pool._semaphore.release()

            def acquire(self):
                return self._AcquireContext(self)

            async def execute(self, *a, **kw):
                async with self.acquire() as conn:
                    return await conn.execute(*a, **kw)

            async def fetch(self, *a, **kw):
                async with self.acquire() as conn:
                    return await conn.fetch(*a, **kw)

            async def fetchrow(self, *a, **kw):
                async with self.acquire() as conn:
                    return await conn.fetchrow(*a, **kw)

            def get_size(self):
                return pool_size

            def get_idle_size(self):
                return pool_size - self._acquired

            def get_min_size(self):
                return 1

            def get_max_size(self):
                return pool_size

            async def close(self):
                pass

        fake_pool = FakePool()
        backend = PostgresBackend.__new__(PostgresBackend)
        backend._pool = fake_pool
        backend._read_pool = fake_pool

        # Fire 20 concurrent list_connected queries
        tasks = [backend.list_connected(f"user-{i}@test.com") for i in range(20)]
        results = await asyncio.gather(*tasks)

        # All should complete (returning empty lists from our mock)
        assert len(results) == 20
        assert all(r == [] for r in results)

        # Verify pool concurrency was capped at pool_size
        assert fake_pool._max_concurrent <= pool_size

    @pytest.mark.asyncio
    async def test_pool_timeout_when_exhausted(self):
        """When pool is fully exhausted, callers should get a timeout error."""
        # Simulate a pool where acquire blocks forever once full
        class BlockingPool:
            def __init__(self):
                self._semaphore = asyncio.Semaphore(0)  # no permits = always blocks

            class _AcquireContext:
                def __init__(self, pool_obj):
                    self._pool = pool_obj

                async def __aenter__(self):
                    # Will block forever
                    await asyncio.wait_for(
                        self._pool._semaphore.acquire(), timeout=0.05
                    )
                    return MagicMock()

                async def __aexit__(self, *args):
                    self._pool._semaphore.release()

            def acquire(self):
                return self._AcquireContext(self)

            async def fetch(self, *a, **kw):
                async with self.acquire() as conn:
                    return []

        from asibot.db_postgres import PostgresBackend

        backend = PostgresBackend.__new__(PostgresBackend)
        blocking_pool = BlockingPool()
        backend._pool = blocking_pool
        backend._read_pool = blocking_pool

        with pytest.raises((asyncio.TimeoutError, TimeoutError)):
            await backend.list_connected("user@test.com")

    @pytest.mark.asyncio
    async def test_pool_not_initialized_raises_runtime_error(self):
        """Calling _get_pool on uninitialized backend should raise RuntimeError."""
        from asibot.db_postgres import PostgresBackend

        backend = PostgresBackend.__new__(PostgresBackend)
        backend._pool = None

        with pytest.raises(RuntimeError, match="not initialised"):
            backend._get_pool()

    @pytest.mark.asyncio
    async def test_concurrent_db_stats_under_pressure(self):
        """db_stats() should work even when pool is under heavy concurrent load."""
        from asibot.db_postgres import PostgresBackend

        call_count = 0

        class FakeConnection:
            async def fetchrow(self, query, *args):
                nonlocal call_count
                call_count += 1
                # Simulate a small delay to create contention
                await asyncio.sleep(0.001)
                return {"cnt": 42}

        class FakePool:
            def __init__(self):
                self._sem = asyncio.Semaphore(3)
                self._acquired = 0

            class _Ctx:
                def __init__(self, pool):
                    self._pool = pool
                async def __aenter__(self):
                    await self._pool._sem.acquire()
                    self._pool._acquired += 1
                    return FakeConnection()
                async def __aexit__(self, *a):
                    self._pool._acquired -= 1
                    self._pool._sem.release()

            def acquire(self):
                return self._Ctx(self)

            async def fetchrow(self, query, *a):
                async with self.acquire() as conn:
                    return await conn.fetchrow(query, *a)

            async def fetch(self, *a, **kw):
                async with self.acquire() as conn:
                    return []

            def get_size(self):
                return 3
            def get_idle_size(self):
                return 3 - self._acquired
            def get_min_size(self):
                return 1
            def get_max_size(self):
                return 3
            async def close(self):
                pass

        fake_pool = FakePool()
        backend = PostgresBackend.__new__(PostgresBackend)
        backend._pool = fake_pool
        backend._read_pool = fake_pool

        # Run multiple db_stats calls concurrently
        tasks = [backend.db_stats() for _ in range(5)]
        results = await asyncio.gather(*tasks)

        assert len(results) == 5
        for stats in results:
            assert "pool_size" in stats
            assert stats["pool_size"] == 3
