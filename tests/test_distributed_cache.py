"""Tests for distributed cache: InMemory, Redis, factory, and S2S token integration."""

import asyncio
import json
import time
from unittest.mock import MagicMock, AsyncMock, patch, PropertyMock

import pytest

from asibot.distributed_cache import (
    DistributedCache,
    InMemoryCache,
    RedisCache,
    create_distributed_cache,
    init_cache,
    get_cache,
    _MAX_S2S_CACHE,
)


# ---- InMemoryCache Tests ----


class TestInMemoryCacheS2STokens:
    @pytest.fixture
    def cache(self):
        return InMemoryCache()

    async def test_get_returns_none_when_empty(self, cache):
        result = await cache.get_s2s_token("zoom:acct1")
        assert result is None

    async def test_put_and_get_token(self, cache):
        expires = time.time() + 3600
        await cache.put_s2s_token("zoom:acct1", "tok_abc", expires)
        result = await cache.get_s2s_token("zoom:acct1")
        assert result is not None
        token, exp = result
        assert token == "tok_abc"
        assert exp == expires

    async def test_expired_token_returns_none(self, cache):
        expires = time.time() - 10  # already expired
        await cache.put_s2s_token("zoom:acct1", "tok_old", expires)
        result = await cache.get_s2s_token("zoom:acct1")
        assert result is None

    async def test_lru_eviction(self, cache):
        # Fill to capacity
        for i in range(_MAX_S2S_CACHE):
            await cache.put_s2s_token(f"key:{i}", f"tok:{i}", time.time() + 3600)

        # All should be present
        assert len(cache._s2s_tokens) == _MAX_S2S_CACHE

        # Add one more — should evict the oldest (key:0)
        await cache.put_s2s_token("key:new", "tok:new", time.time() + 3600)
        assert len(cache._s2s_tokens) == _MAX_S2S_CACHE
        assert await cache.get_s2s_token("key:0") is None
        result = await cache.get_s2s_token("key:new")
        assert result is not None
        assert result[0] == "tok:new"

    async def test_put_updates_existing(self, cache):
        await cache.put_s2s_token("k1", "old_tok", time.time() + 100)
        await cache.put_s2s_token("k1", "new_tok", time.time() + 3600)
        result = await cache.get_s2s_token("k1")
        assert result[0] == "new_tok"

    async def test_get_moves_to_end_lru(self, cache):
        """Accessing a key should move it to the end (most recently used)."""
        await cache.put_s2s_token("a", "ta", time.time() + 3600)
        await cache.put_s2s_token("b", "tb", time.time() + 3600)
        # Access "a" to move it to end
        await cache.get_s2s_token("a")
        # "b" should now be the oldest
        keys = list(cache._s2s_tokens.keys())
        assert keys[-1] == "a"


class TestInMemoryCacheRateLimit:
    @pytest.fixture
    def cache(self):
        return InMemoryCache()

    async def test_under_limit_returns_true(self, cache):
        for _ in range(5):
            assert await cache.check_rate_limit("svc:zoom", 10, 60) is True

    async def test_at_limit_returns_false(self, cache):
        for _ in range(3):
            await cache.check_rate_limit("svc:test", 3, 60)
        # 4th should be rejected
        assert await cache.check_rate_limit("svc:test", 3, 60) is False

    async def test_window_expiry_allows_new_requests(self, cache):
        # Fill the limit
        for _ in range(3):
            await cache.check_rate_limit("svc:test", 3, 1)
        assert await cache.check_rate_limit("svc:test", 3, 1) is False

        # Wait for window to expire
        await asyncio.sleep(1.1)
        assert await cache.check_rate_limit("svc:test", 3, 1) is True

    async def test_different_keys_independent(self, cache):
        for _ in range(5):
            await cache.check_rate_limit("svc:a", 5, 60)
        # "a" is at limit
        assert await cache.check_rate_limit("svc:a", 5, 60) is False
        # "b" should still be fine
        assert await cache.check_rate_limit("svc:b", 5, 60) is True


class TestInMemoryCacheCleanup:
    async def test_cleanup_removes_expired_tokens(self):
        cache = InMemoryCache()
        await cache.put_s2s_token("expired", "tok1", time.time() - 10)
        await cache.put_s2s_token("valid", "tok2", time.time() + 3600)
        await cache.cleanup()
        assert "expired" not in cache._s2s_tokens
        assert "valid" in cache._s2s_tokens

    async def test_cleanup_removes_empty_rate_entries(self):
        cache = InMemoryCache()
        # Add and then empty a rate limit entry
        cache._rate_limits["empty_key"] = __import__("collections").deque()
        cache._rate_limits["nonempty"] = __import__("collections").deque([time.time()])
        await cache.cleanup()
        assert "empty_key" not in cache._rate_limits
        assert "nonempty" in cache._rate_limits


# ---- RedisCache Tests ----


class TestRedisCacheS2STokens:
    @pytest.fixture
    def redis_mock(self):
        return MagicMock()

    @pytest.fixture
    def cache(self, redis_mock):
        return RedisCache(redis_mock)

    async def test_get_calls_redis_get(self, cache, redis_mock):
        redis_mock.get.return_value = json.dumps({"token": "tok1", "expires_at": time.time() + 3600})
        result = await cache.get_s2s_token("zoom:acct1")
        redis_mock.get.assert_called_once_with("asibot:s2s:zoom:acct1")
        assert result is not None
        assert result[0] == "tok1"

    async def test_get_returns_none_when_not_found(self, cache, redis_mock):
        redis_mock.get.return_value = None
        result = await cache.get_s2s_token("zoom:acct1")
        assert result is None

    async def test_get_expired_deletes_and_returns_none(self, cache, redis_mock):
        redis_mock.get.return_value = json.dumps({"token": "tok1", "expires_at": time.time() - 10})
        result = await cache.get_s2s_token("zoom:acct1")
        assert result is None
        redis_mock.delete.assert_called_once_with("asibot:s2s:zoom:acct1")

    async def test_put_calls_setex(self, cache, redis_mock):
        expires_at = time.time() + 3600
        await cache.put_s2s_token("zoom:acct1", "tok_new", expires_at)
        redis_mock.setex.assert_called_once()
        args = redis_mock.setex.call_args
        assert args[0][0] == "asibot:s2s:zoom:acct1"
        # TTL should be approximately 3600
        assert 3590 <= args[0][1] <= 3600
        payload = json.loads(args[0][2])
        assert payload["token"] == "tok_new"

    async def test_get_graceful_on_redis_error(self, cache, redis_mock):
        redis_mock.get.side_effect = Exception("connection lost")
        result = await cache.get_s2s_token("zoom:acct1")
        assert result is None  # fails open

    async def test_put_graceful_on_redis_error(self, cache, redis_mock):
        redis_mock.setex.side_effect = Exception("connection lost")
        # Should not raise
        await cache.put_s2s_token("zoom:acct1", "tok", time.time() + 3600)


class TestRedisCacheRateLimit:
    @pytest.fixture
    def redis_mock(self):
        return MagicMock()

    @pytest.fixture
    def cache(self, redis_mock):
        return RedisCache(redis_mock)

    async def test_under_limit(self, cache, redis_mock):
        redis_mock.incr.return_value = 5
        result = await cache.check_rate_limit("svc:zoom", 10, 60)
        assert result is True

    async def test_at_limit(self, cache, redis_mock):
        redis_mock.incr.return_value = 11
        result = await cache.check_rate_limit("svc:zoom", 10, 60)
        assert result is False

    async def test_first_request_sets_expire(self, cache, redis_mock):
        redis_mock.incr.return_value = 1
        await cache.check_rate_limit("svc:zoom", 10, 60)
        redis_mock.expire.assert_called_once()
        expire_args = redis_mock.expire.call_args[0]
        assert expire_args[1] == 60  # window_seconds

    async def test_subsequent_request_no_expire(self, cache, redis_mock):
        redis_mock.incr.return_value = 5
        await cache.check_rate_limit("svc:zoom", 10, 60)
        redis_mock.expire.assert_not_called()

    async def test_graceful_on_redis_error(self, cache, redis_mock):
        redis_mock.incr.side_effect = Exception("connection lost")
        result = await cache.check_rate_limit("svc:zoom", 10, 60)
        assert result is True  # fails open

    async def test_cleanup_is_noop(self, cache):
        # Redis handles TTL automatically
        await cache.cleanup()  # should not raise


# ---- Factory Tests ----


class TestCreateDistributedCache:
    async def test_memory_fallback_default(self):
        """With default settings (memory backend), returns InMemoryCache."""
        from asibot.config import settings as real_settings
        with (
            patch.object(real_settings, "session_backend", "memory"),
            patch.object(real_settings, "redis_url", ""),
        ):
            cache = await create_distributed_cache()
            assert isinstance(cache, InMemoryCache)

    async def test_redis_selection(self):
        """When backend=redis and redis_url set, attempts Redis."""
        mock_redis_client = MagicMock()
        mock_redis_client.ping.return_value = True
        mock_redis_module = MagicMock()
        mock_redis_module.Redis.from_url.return_value = mock_redis_client

        from asibot.config import settings as real_settings
        with (
            patch.object(real_settings, "session_backend", "redis"),
            patch.object(real_settings, "redis_url", "redis://localhost:6379/0"),
            patch.dict("sys.modules", {"redis": mock_redis_module}),
        ):
            import asibot.distributed_cache as dc_mod
            cache = await dc_mod.create_distributed_cache()
            assert isinstance(cache, RedisCache)

    async def test_redis_import_failure_falls_back(self):
        """If redis package not installed, falls back to InMemoryCache."""
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "redis":
                raise ImportError("No module named 'redis'")
            return real_import(name, *args, **kwargs)

        from asibot.config import settings as real_settings
        with (
            patch.object(real_settings, "session_backend", "redis"),
            patch.object(real_settings, "redis_url", "redis://localhost:6379/0"),
            patch("builtins.__import__", side_effect=mock_import),
        ):
            import asibot.distributed_cache as dc_mod
            cache = await dc_mod.create_distributed_cache()
            assert isinstance(cache, InMemoryCache)

    async def test_redis_connection_failure_falls_back(self):
        """If Redis is unreachable, falls back to InMemoryCache."""
        mock_redis_module = MagicMock()
        mock_redis_module.Redis.from_url.return_value.ping.side_effect = Exception("Connection refused")

        from asibot.config import settings as real_settings
        with (
            patch.object(real_settings, "session_backend", "redis"),
            patch.object(real_settings, "redis_url", "redis://localhost:6379/0"),
            patch.dict("sys.modules", {"redis": mock_redis_module}),
        ):
            import asibot.distributed_cache as dc_mod
            cache = await dc_mod.create_distributed_cache()
            assert isinstance(cache, InMemoryCache)


# ---- Singleton Tests ----


class TestCacheSingleton:
    async def test_get_cache_before_init_raises(self):
        import asibot.distributed_cache as dc
        original = dc._cache
        try:
            dc._cache = None
            with pytest.raises(RuntimeError, match="not initialized"):
                get_cache()
        finally:
            dc._cache = original

    async def test_init_cache_sets_singleton(self):
        import asibot.distributed_cache as dc
        original = dc._cache
        try:
            dc._cache = None
            with patch("asibot.distributed_cache.create_distributed_cache") as mock_create:
                mock_cache = InMemoryCache()
                mock_create.return_value = mock_cache
                result = await init_cache()
                assert result is mock_cache
                assert get_cache() is mock_cache
        finally:
            dc._cache = original


# ---- Integration: get_s2s_token in token_store ----


class TestGetS2STokenIntegration:
    """Test token_store.get_s2s_token() using a real InMemoryCache."""

    @pytest.fixture
    def memory_cache(self):
        return InMemoryCache()

    async def test_fetches_and_caches_token(self, memory_cache):
        """First call fetches from endpoint; second returns cached."""
        import asibot.distributed_cache as dc
        import asibot.token_store as ts

        original = dc._cache
        dc._cache = memory_cache

        try:
            call_count = 0

            async def mock_post(self, url, **kwargs):
                nonlocal call_count
                call_count += 1
                resp = MagicMock()
                resp.raise_for_status = MagicMock()
                resp.json.return_value = {"access_token": f"tok_{call_count}", "expires_in": 3600}
                return resp

            with patch("httpx.AsyncClient.post", mock_post):
                # First call — should hit the endpoint
                token1 = await ts.get_s2s_token(
                    cache_key="test:acct1",
                    token_url="https://example.com/token",
                    grant_data={"grant_type": "client_credentials"},
                    auth=("client_id", "secret"),
                    service_name="Test",
                )
                assert token1 == "tok_1"
                assert call_count == 1

                # Second call — should return cached
                token2 = await ts.get_s2s_token(
                    cache_key="test:acct1",
                    token_url="https://example.com/token",
                    grant_data={"grant_type": "client_credentials"},
                    auth=("client_id", "secret"),
                    service_name="Test",
                )
                assert token2 == "tok_1"  # same token, from cache
                assert call_count == 1  # no additional fetch
        finally:
            dc._cache = original

    async def test_expired_token_refetched(self, memory_cache):
        """When cached token is within margin of expiry, re-fetch."""
        import asibot.distributed_cache as dc
        import asibot.token_store as ts

        original = dc._cache
        dc._cache = memory_cache

        try:
            # Pre-populate cache with nearly-expired token
            await memory_cache.put_s2s_token("test:acct2", "old_tok", time.time() + 100)

            async def mock_post(self, url, **kwargs):
                resp = MagicMock()
                resp.raise_for_status = MagicMock()
                resp.json.return_value = {"access_token": "fresh_tok", "expires_in": 3600}
                return resp

            with patch("httpx.AsyncClient.post", mock_post):
                # Token is within margin (100 < 300), should re-fetch
                token = await ts.get_s2s_token(
                    cache_key="test:acct2",
                    token_url="https://example.com/token",
                    grant_data={"grant_type": "client_credentials"},
                    auth=("cid", "sec"),
                    service_name="Test",
                )
                assert token == "fresh_tok"
        finally:
            dc._cache = original

    async def test_send_as_params(self, memory_cache):
        """send_as_params=True should send grant_data as query params."""
        import asibot.distributed_cache as dc
        import asibot.token_store as ts

        original = dc._cache
        dc._cache = memory_cache

        try:
            captured_kwargs = {}

            async def mock_post(self, url, **kwargs):
                captured_kwargs.update(kwargs)
                resp = MagicMock()
                resp.raise_for_status = MagicMock()
                resp.json.return_value = {"access_token": "tok", "expires_in": 3600}
                return resp

            with patch("httpx.AsyncClient.post", mock_post):
                await ts.get_s2s_token(
                    cache_key="test:params",
                    token_url="https://example.com/token",
                    grant_data={"grant_type": "account_credentials", "account_id": "x"},
                    auth=("cid", "sec"),
                    service_name="Test",
                    send_as_params=True,
                )
                assert "params" in captured_kwargs
                assert "data" not in captured_kwargs
        finally:
            dc._cache = original

    async def test_missing_access_token_raises(self, memory_cache):
        """Should raise ValueError if response lacks access_token."""
        import asibot.distributed_cache as dc
        import asibot.token_store as ts

        original = dc._cache
        dc._cache = memory_cache

        try:

            async def mock_post(self, url, **kwargs):
                resp = MagicMock()
                resp.raise_for_status = MagicMock()
                resp.json.return_value = {"error": "invalid_grant"}
                return resp

            with patch("httpx.AsyncClient.post", mock_post):
                with pytest.raises(ValueError, match="missing access_token"):
                    await ts.get_s2s_token(
                        cache_key="test:err",
                        token_url="https://example.com/token",
                        grant_data={"grant_type": "client_credentials"},
                        auth=("cid", "sec"),
                        service_name="TestSvc",
                    )
        finally:
            dc._cache = original


# ---- Integration: check_service_rate_limit ----


class TestCheckServiceRateLimit:
    async def test_under_limit_returns_true(self):
        import asibot.distributed_cache as dc
        import asibot.token_store as ts

        original = dc._cache
        dc._cache = InMemoryCache()
        try:
            result = await ts.check_service_rate_limit("zoom", limit=5, window_seconds=60)
            assert result is True
        finally:
            dc._cache = original

    async def test_over_limit_returns_false(self):
        import asibot.distributed_cache as dc
        import asibot.token_store as ts

        original = dc._cache
        dc._cache = InMemoryCache()
        try:
            for _ in range(3):
                await ts.check_service_rate_limit("zoom", limit=3, window_seconds=60)
            result = await ts.check_service_rate_limit("zoom", limit=3, window_seconds=60)
            assert result is False
        finally:
            dc._cache = original


# ---- Config production warning ----


class TestConfigProductionWarning:
    def test_memory_with_http_warns(self):
        from asibot.config import Settings

        s = Settings(
            transport="streamable-http",
            session_backend="memory",
            redis_url="",
        )
        warns = s.validate_for_production()
        assert len(warns) == 1
        assert "session_backend=memory" in warns[0]

    def test_redis_with_http_no_warning(self):
        from asibot.config import Settings

        s = Settings(
            transport="streamable-http",
            session_backend="redis",
            redis_url="redis://localhost:6379/0",
        )
        warns = s.validate_for_production()
        assert len(warns) == 0

    def test_memory_with_stdio_no_warning(self):
        from asibot.config import Settings

        s = Settings(
            transport="stdio",
            session_backend="memory",
        )
        warns = s.validate_for_production()
        assert len(warns) == 0
