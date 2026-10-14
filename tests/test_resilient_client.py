"""
Tests for the resilient HTTP client (ResilientClient).

These tests verify the most critical production-safety features:
- Exponential backoff on transient errors (429, 5xx).
- Rate limiting via token bucket.
- Retry-After header support.
- Permanent error propagation (no retry on 4xx).
"""

import time
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.clients import (
    ResilientClient,
    TokenBucketRateLimiter,
    TransientAPIError,
    PermanentAPIError,
)


# ── TokenBucketRateLimiter ───────────────────────────────────────────


class TestTokenBucketRateLimiter:
    """Tests for the token-bucket rate limiter."""

    def test_allows_burst_up_to_capacity(self):
        """A fresh bucket should allow *capacity* requests immediately."""
        limiter = TokenBucketRateLimiter(rate=10.0, capacity=5)
        start = time.monotonic()
        for _ in range(5):
            limiter.acquire()
        elapsed = time.monotonic() - start
        # All 5 should complete almost instantly (burst).
        assert elapsed < 0.5

    def test_enforces_rate_after_burst(self):
        """After the burst capacity is exhausted, subsequent requests
        should be delayed to match the configured rate."""
        limiter = TokenBucketRateLimiter(rate=10.0, capacity=1)
        # Exhaust the single token.
        limiter.acquire()
        start = time.monotonic()
        limiter.acquire()
        elapsed = time.monotonic() - start
        # Should have waited ~0.1s (1/10 rps).
        assert elapsed >= 0.05


# ── ResilientClient ──────────────────────────────────────────────────


class TestResilientClient:
    """Tests for the ResilientClient HTTP wrapper."""

    def _make_client(self, **overrides) -> ResilientClient:
        defaults = {
            "base_url": "http://test-api",
            "api_key": "test-key",
            "max_rps": 100.0,  # High limit so rate limiter doesn't delay tests.
            "max_retries": 3,
            "base_delay": 0.01,  # Short delays for fast tests.
            "max_delay": 0.1,
        }
        defaults.update(overrides)
        return ResilientClient(**defaults)

    def test_successful_get(self):
        """A 200 response should return the parsed JSON."""
        client = self._make_client()
        mock_response = httpx.Response(
            200,
            json={"data": "ok"},
            request=httpx.Request("GET", "http://test-api/test"),
        )
        with patch.object(client._client, "request", return_value=mock_response):
            result = client.get("/test")
        assert result == {"data": "ok"}

    def test_successful_post(self):
        """A 200 POST response should return the parsed JSON."""
        client = self._make_client()
        mock_response = httpx.Response(
            200,
            json={"accepted": ["SKU1"]},
            request=httpx.Request("POST", "http://test-api/batch"),
        )
        with patch.object(client._client, "request", return_value=mock_response):
            result = client.post("/batch", json={"products": []})
        assert result == {"accepted": ["SKU1"]}

    def test_retries_on_429(self):
        """HTTP 429 should trigger a retry, and succeed on the second attempt."""
        client = self._make_client()
        fail_response = httpx.Response(
            429,
            text="rate limited",
            headers={"Retry-After": "0"},
            request=httpx.Request("GET", "http://test-api/test"),
        )
        ok_response = httpx.Response(
            200,
            json={"data": "ok"},
            request=httpx.Request("GET", "http://test-api/test"),
        )
        with patch.object(
            client._client, "request", side_effect=[fail_response, ok_response]
        ):
            result = client.get("/test")
        assert result == {"data": "ok"}

    def test_retries_on_503(self):
        """HTTP 503 should trigger a retry."""
        client = self._make_client()
        fail_response = httpx.Response(
            503,
            text="unavailable",
            headers={"Retry-After": "0"},
            request=httpx.Request("GET", "http://test-api/test"),
        )
        ok_response = httpx.Response(
            200,
            json={"data": "recovered"},
            request=httpx.Request("GET", "http://test-api/test"),
        )
        with patch.object(
            client._client, "request", side_effect=[fail_response, ok_response]
        ):
            result = client.get("/test")
        assert result == {"data": "recovered"}

    def test_raises_after_max_retries(self):
        """After exhausting retries, should raise TransientAPIError."""
        client = self._make_client(max_retries=2)
        fail_response = httpx.Response(
            500,
            text="server error",
            request=httpx.Request("GET", "http://test-api/test"),
        )
        with patch.object(
            client._client, "request", return_value=fail_response
        ):
            with pytest.raises(TransientAPIError):
                client.get("/test")

    def test_raises_on_permanent_error(self):
        """HTTP 400/401/403 should raise PermanentAPIError immediately (no retry)."""
        client = self._make_client()
        bad_response = httpx.Response(
            401,
            text="unauthorized",
            request=httpx.Request("GET", "http://test-api/test"),
        )
        with patch.object(client._client, "request", return_value=bad_response):
            with pytest.raises(PermanentAPIError) as exc_info:
                client.get("/test")
            assert exc_info.value.status_code == 401

    def test_respects_retry_after_header(self):
        """When Retry-After is present, the client should sleep for that duration."""
        client = self._make_client(max_retries=2)
        fail_response = httpx.Response(
            429,
            text="rate limited",
            headers={"Retry-After": "0.01"},
            request=httpx.Request("GET", "http://test-api/test"),
        )
        ok_response = httpx.Response(
            200,
            json={"data": "ok"},
            request=httpx.Request("GET", "http://test-api/test"),
        )
        with patch.object(
            client._client, "request", side_effect=[fail_response, ok_response]
        ):
            start = time.monotonic()
            result = client.get("/test")
            elapsed = time.monotonic() - start
        assert result == {"data": "ok"}
        # Should have slept at least the Retry-After value.
        assert elapsed >= 0.01


# ── API key header ───────────────────────────────────────────────────


class TestAPIKeyPropagation:
    """Ensure the API key is sent in request headers."""

    def test_api_key_in_headers(self):
        client = ResilientClient(
            base_url="http://test",
            api_key="my-secret-key",
            max_rps=100,
        )
        mock_response = httpx.Response(
            200,
            json={},
            request=httpx.Request("GET", "http://test/x"),
        )
        with patch.object(client._client, "request", return_value=mock_response) as mock_req:
            client.get("/x")
        _, kwargs = mock_req.call_args
        assert kwargs["headers"]["X-API-Key"] == "my-secret-key"
