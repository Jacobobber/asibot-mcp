"""Tests for retry/backoff logic in safe_request and per-user rate limiting integration."""

import asyncio
import time
from collections import deque
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from asibot import token_store
from asibot.config import settings


def _mock_response(status_code: int, json_data=None, headers=None):
    """Create a mock httpx.Response."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.headers = headers or {}
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}",
            request=MagicMock(),
            response=resp,
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


def _mock_client(response_or_side_effect):
    """Create a mock httpx.AsyncClient."""
    client = AsyncMock(spec=httpx.AsyncClient)
    if isinstance(response_or_side_effect, list):
        client.request.side_effect = response_or_side_effect
    else:
        client.request.return_value = response_or_side_effect
    return client


class TestRetryOnTransientErrors:
    """Retry logic should retry on 429, 502, 503, 504, and network errors."""

    @pytest.mark.asyncio
    async def test_retry_on_502_then_succeed(self):
        resp_502 = _mock_response(502)
        resp_200 = _mock_response(200, {"ok": True})
        client = _mock_client([resp_502, resp_200])

        with patch.object(settings, "max_retries", 3), \
             patch.object(settings, "retry_base_delay", 0.01):
            r, err = await token_store.safe_request(
                client, "GET", "https://api.example.com/test",
                service="Test", action="fetch",
            )
        assert r is not None
        assert err is None
        assert r.json() == {"ok": True}
        assert client.request.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_on_503_then_succeed(self):
        resp_503 = _mock_response(503)
        resp_200 = _mock_response(200, {"ok": True})
        client = _mock_client([resp_503, resp_200])

        with patch.object(settings, "max_retries", 3), \
             patch.object(settings, "retry_base_delay", 0.01):
            r, err = await token_store.safe_request(
                client, "GET", "https://api.example.com/test",
                service="Test", action="fetch",
            )
        assert r is not None
        assert err is None
        assert client.request.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_on_504_then_succeed(self):
        resp_504 = _mock_response(504)
        resp_200 = _mock_response(200, {"ok": True})
        client = _mock_client([resp_504, resp_200])

        with patch.object(settings, "max_retries", 3), \
             patch.object(settings, "retry_base_delay", 0.01):
            r, err = await token_store.safe_request(
                client, "GET", "https://api.example.com/test",
                service="Test", action="fetch",
            )
        assert r is not None
        assert err is None
        assert client.request.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_on_429_then_succeed(self):
        resp_429 = _mock_response(429)
        resp_200 = _mock_response(200, {"ok": True})
        client = _mock_client([resp_429, resp_200])

        with patch.object(settings, "max_retries", 3), \
             patch.object(settings, "retry_base_delay", 0.01):
            r, err = await token_store.safe_request(
                client, "GET", "https://api.example.com/test",
                service="Test", action="fetch",
            )
        assert r is not None
        assert err is None
        assert client.request.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_on_network_error_then_succeed(self):
        resp_200 = _mock_response(200, {"ok": True})
        client = AsyncMock(spec=httpx.AsyncClient)
        client.request.side_effect = [
            httpx.RequestError("Connection reset"),
            resp_200,
        ]

        with patch.object(settings, "max_retries", 3), \
             patch.object(settings, "retry_base_delay", 0.01):
            r, err = await token_store.safe_request(
                client, "GET", "https://api.example.com/test",
                service="Test", action="fetch",
            )
        assert r is not None
        assert err is None
        assert client.request.call_count == 2

    @pytest.mark.asyncio
    async def test_exhausted_retries_returns_last_error(self):
        resp_502 = _mock_response(502)
        client = _mock_client([resp_502, resp_502, resp_502, resp_502])

        with patch.object(settings, "max_retries", 3), \
             patch.object(settings, "retry_base_delay", 0.01):
            r, err = await token_store.safe_request(
                client, "GET", "https://api.example.com/test",
                service="Test", action="fetch",
            )
        assert r is None
        assert "502" in err
        assert "Test fetch failed" in err
        # initial + 3 retries = 4 total attempts
        assert client.request.call_count == 4


class TestNoRetryOnClientErrors:
    """Client errors (400, 401, 403, 404, 422) should NOT be retried."""

    @pytest.mark.asyncio
    async def test_no_retry_on_400(self):
        resp_400 = _mock_response(400)
        client = _mock_client(resp_400)

        with patch.object(settings, "max_retries", 3), \
             patch.object(settings, "retry_base_delay", 0.01):
            r, err = await token_store.safe_request(
                client, "GET", "https://api.example.com/test",
                service="Test", action="fetch",
            )
        assert r is None
        assert "400" in err
        assert client.request.call_count == 1

    @pytest.mark.asyncio
    async def test_no_retry_on_401(self):
        resp_401 = _mock_response(401)
        client = _mock_client(resp_401)

        with patch.object(settings, "max_retries", 3), \
             patch.object(settings, "retry_base_delay", 0.01):
            r, err = await token_store.safe_request(
                client, "GET", "https://api.example.com/test",
                service="Test", action="fetch",
            )
        assert r is None
        assert "401" in err
        assert client.request.call_count == 1

    @pytest.mark.asyncio
    async def test_no_retry_on_403(self):
        resp_403 = _mock_response(403)
        client = _mock_client(resp_403)

        with patch.object(settings, "max_retries", 3), \
             patch.object(settings, "retry_base_delay", 0.01):
            r, err = await token_store.safe_request(
                client, "GET", "https://api.example.com/test",
                service="Test", action="fetch",
            )
        assert r is None
        assert "403" in err
        assert client.request.call_count == 1

    @pytest.mark.asyncio
    async def test_no_retry_on_404(self):
        resp_404 = _mock_response(404)
        client = _mock_client(resp_404)

        with patch.object(settings, "max_retries", 3), \
             patch.object(settings, "retry_base_delay", 0.01):
            r, err = await token_store.safe_request(
                client, "GET", "https://api.example.com/test",
                service="Test", action="fetch",
            )
        assert r is None
        assert "404" in err
        assert client.request.call_count == 1

    @pytest.mark.asyncio
    async def test_no_retry_on_422(self):
        resp_422 = _mock_response(422)
        client = _mock_client(resp_422)

        with patch.object(settings, "max_retries", 3), \
             patch.object(settings, "retry_base_delay", 0.01):
            r, err = await token_store.safe_request(
                client, "GET", "https://api.example.com/test",
                service="Test", action="fetch",
            )
        assert r is None
        assert "422" in err
        assert client.request.call_count == 1


class TestRetryAfterHeader:
    """429 responses with Retry-After header should use that delay."""

    @pytest.mark.asyncio
    async def test_respects_retry_after_header(self):
        resp_429 = _mock_response(429, headers={"retry-after": "0.01"})
        resp_200 = _mock_response(200, {"ok": True})
        client = _mock_client([resp_429, resp_200])

        with patch.object(settings, "max_retries", 3), \
             patch.object(settings, "retry_base_delay", 0.01):
            r, err = await token_store.safe_request(
                client, "GET", "https://api.example.com/test",
                service="Test", action="fetch",
            )
        assert r is not None
        assert err is None

    @pytest.mark.asyncio
    async def test_invalid_retry_after_uses_default(self):
        resp_429 = _mock_response(429, headers={"retry-after": "not-a-number"})
        resp_200 = _mock_response(200, {"ok": True})
        client = _mock_client([resp_429, resp_200])

        with patch.object(settings, "max_retries", 3), \
             patch.object(settings, "retry_base_delay", 0.01):
            r, err = await token_store.safe_request(
                client, "GET", "https://api.example.com/test",
                service="Test", action="fetch",
            )
        assert r is not None
        assert err is None


class TestPerUserServiceRateLimit:
    """Per-user rate limiting via _check_service_rate_limit."""

    def setup_method(self):
        token_store._service_rate.clear()

    def teardown_method(self):
        token_store._service_rate.clear()

    def test_allows_within_limit(self):
        """Requests within the limit should pass."""
        err = token_store._check_service_rate_limit("user@example.com", "github")
        assert err is None

    def test_blocks_at_limit(self):
        """Requests at the limit should be blocked."""
        for _ in range(token_store._SERVICE_RATE_LIMIT):
            token_store._check_service_rate_limit("user@example.com", "github")
        err = token_store._check_service_rate_limit("user@example.com", "github")
        assert err is not None
        assert "Rate limit exceeded" in err

    def test_different_users_separate(self):
        """Different users have separate rate limit buckets."""
        for _ in range(token_store._SERVICE_RATE_LIMIT):
            token_store._check_service_rate_limit("alice@example.com", "github")
        # Alice is blocked
        assert token_store._check_service_rate_limit("alice@example.com", "github") is not None
        # Bob is not
        assert token_store._check_service_rate_limit("bob@example.com", "github") is None

    def test_different_services_separate(self):
        """Different services have separate rate limit buckets."""
        for _ in range(token_store._SERVICE_RATE_LIMIT):
            token_store._check_service_rate_limit("user@example.com", "github")
        # GitHub is blocked
        assert token_store._check_service_rate_limit("user@example.com", "github") is not None
        # Jira is not
        assert token_store._check_service_rate_limit("user@example.com", "jira") is None

    def test_cleanup_removes_stale(self):
        """cleanup_rate_limits should remove stale entries."""
        token_store._check_service_rate_limit("user@example.com", "github")
        assert len(token_store._service_rate) > 0
        # Manually expire all entries
        for k in token_store._service_rate:
            token_store._service_rate[k] = deque([time.time() - 120])
        removed = token_store.cleanup_rate_limits()
        assert removed > 0

class TestRetryableStatusCodes:
    """Verify that _RETRYABLE_STATUS contains the right codes."""

    def test_retryable_set(self):
        """429, 500, 502, 503, 504 should be retryable."""
        assert 429 in token_store._RETRYABLE_STATUS
        assert 500 in token_store._RETRYABLE_STATUS
        assert 502 in token_store._RETRYABLE_STATUS
        assert 503 in token_store._RETRYABLE_STATUS
        assert 504 in token_store._RETRYABLE_STATUS

    def test_not_retryable(self):
        """Client errors should NOT be retryable."""
        assert 400 not in token_store._RETRYABLE_STATUS
        assert 401 not in token_store._RETRYABLE_STATUS
        assert 403 not in token_store._RETRYABLE_STATUS
        assert 404 not in token_store._RETRYABLE_STATUS
        assert 422 not in token_store._RETRYABLE_STATUS


class TestRetryMaxRetries:
    """Verify max_retries parameter controls retry count."""

    @pytest.mark.asyncio
    async def test_respects_max_retries(self):
        """safe_request with max_retries=1 should make at most 2 attempts."""
        resp_502 = _mock_response(502)
        client = _mock_client(resp_502)

        r, err = await token_store.safe_request(
            client, "GET", "https://api.example.com/test",
            service="Test", action="fetch",
            max_retries=1,
        )
        assert r is None
        assert "502" in err
        # 1 initial + 1 retry = 2 calls
        assert client.request.call_count == 2
