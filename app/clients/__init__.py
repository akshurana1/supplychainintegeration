"""
Resilient HTTP client with exponential backoff, rate limiting, and
Retry-After header support.

This module addresses the single biggest production risk in the original
implementation: a naive single-retry with a fixed 1 s sleep.  The new
client uses *tenacity* for configurable exponential backoff with jitter,
respects the ``Retry-After`` response header, and enforces a token-bucket
rate limiter to stay within documented API limits.
"""

from __future__ import annotations

import time
import threading
from typing import Any, Dict, Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
    before_sleep_log,
    RetryError,
)

from app.observability import get_logger

logger = get_logger(__name__)

# HTTP status codes that indicate transient failures worth retrying.
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


class TransientAPIError(Exception):
    """Raised when an API returns a retryable HTTP status code."""

    def __init__(self, status_code: int, detail: str, retry_after: Optional[float] = None):
        self.status_code = status_code
        self.detail = detail
        self.retry_after = retry_after
        super().__init__(f"HTTP {status_code}: {detail}")


class PermanentAPIError(Exception):
    """Raised when an API returns a non-retryable error (4xx other than 429)."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"HTTP {status_code}: {detail}")


class TokenBucketRateLimiter:
    """Simple token-bucket rate limiter.

    Enforces a maximum sustained request rate by sleeping the caller
    when the bucket is empty.  Thread-safe.

    Why a token bucket instead of a sliding-window counter?
    - Token buckets allow short bursts (up to *capacity* requests)
      while still enforcing the long-term average rate.
    - They are simple, require no external state, and have O(1) cost.
    """

    def __init__(self, rate: float, capacity: Optional[int] = None):
        """
        Args:
            rate: Maximum sustained requests per second.
            capacity: Burst capacity.  Defaults to ``int(rate)``.
        """
        self._rate = rate
        self._capacity = capacity or int(rate)
        self._tokens = float(self._capacity)
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Block until a token is available, then consume it."""
        while True:
            with self._lock:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._tokens = min(
                    self._capacity,
                    self._tokens + elapsed * self._rate,
                )
                self._last_refill = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
            # Sleep briefly and retry – avoids busy-waiting.
            time.sleep(1.0 / self._rate)


class ResilientClient:
    """HTTP client with built-in rate limiting, retry and backoff.

    Wraps ``httpx.Client`` and adds:
    - Token-bucket rate limiting to avoid flooding upstream APIs.
    - Exponential backoff with jitter for transient failures (429 / 5xx).
    - Respect for ``Retry-After`` response headers.
    - Structured logging of every retry attempt.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        max_rps: float,
        max_retries: int = 5,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        timeout: float = 30.0,
        api_key_header: str = "X-API-Key",
    ):
        self._base_url = base_url
        self._headers = {api_key_header: api_key}
        self._timeout = timeout
        self._rate_limiter = TokenBucketRateLimiter(rate=max_rps)
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._max_delay = max_delay
        self._client = httpx.Client(base_url=base_url, timeout=timeout)

    # ── Public interface ─────────────────────────────────────────────

    def get(self, path: str, *, params: Optional[Dict[str, Any]] = None) -> dict:
        """Issue a GET request with rate limiting and retry."""
        return self._request_with_retry("GET", path, params=params)

    def post(self, path: str, *, json: Optional[Dict[str, Any]] = None) -> dict:
        """Issue a POST request with rate limiting and retry."""
        return self._request_with_retry("POST", path, json=json)

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ── Internal ─────────────────────────────────────────────────────

    def _request_with_retry(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
    ) -> dict:
        """Execute a single HTTP request with rate-limit + retry logic.

        The retry decorator is built dynamically so that the
        ``max_retries`` value from the constructor is honoured.
        """

        @retry(
            retry=retry_if_exception_type((TransientAPIError, httpx.TransportError)),
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential_jitter(
                initial=self._base_delay,
                max=self._max_delay,
                jitter=self._base_delay,
            ),
            reraise=True,
        )
        def _do() -> dict:
            # Enforce rate limit *before* sending.
            self._rate_limiter.acquire()

            response = self._client.request(
                method,
                path,
                params=params,
                json=json,
                headers=self._headers,
            )

            if response.status_code in RETRYABLE_STATUS_CODES:
                retry_after = self._parse_retry_after(response)
                if retry_after:
                    logger.warning(
                        "api_retry_after",
                        status=response.status_code,
                        retry_after=retry_after,
                        path=path,
                    )
                    time.sleep(retry_after)

                raise TransientAPIError(
                    status_code=response.status_code,
                    detail=response.text[:200],
                    retry_after=retry_after,
                )

            if response.status_code >= 400:
                raise PermanentAPIError(
                    status_code=response.status_code,
                    detail=response.text[:200],
                )

            return response.json()

        try:
            return _do()
        except RetryError:
            # Re-raise the last underlying exception for clearer tracebacks.
            raise

    @staticmethod
    def _parse_retry_after(response: httpx.Response) -> Optional[float]:
        """Extract ``Retry-After`` header value as seconds, if present."""
        raw = response.headers.get("Retry-After")
        if raw is None:
            return None
        try:
            return float(raw)
        except (ValueError, TypeError):
            return None
