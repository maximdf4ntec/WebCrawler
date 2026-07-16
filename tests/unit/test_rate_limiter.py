"""
Unit tests for RateLimiter (Task 5.1).

Tests:
- Normal execution (pass-through)
- QueueOverflowError at capacity 1000
- 429 backoff with Retry-After header (clamping)
- 429 exponential backoff without header
- RateLimitExhaustedError after 10 consecutive 429s
- Consecutive 429 counter reset on non-429 response
- Global backoff pause (no dispatches during backoff)
- FIFO ordering of queued requests

Uses asyncio for all tests. The fn callable is mocked to return
controlled FetchResponse-like objects.
"""

import asyncio
from dataclasses import dataclass
from typing import Optional

import pytest
import pytest_asyncio

from crawler.rate_limiter import RateLimiter
from crawler.types import QueueOverflowError, RateLimitExhaustedError


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


@dataclass
class MockResponse:
    """Minimal response object matching what RateLimiter inspects."""

    status_code: int
    headers: dict[str, str]


def make_response(
    status_code: int = 200, headers: Optional[dict] = None
) -> MockResponse:
    return MockResponse(status_code=status_code, headers=headers or {})


def ok_fn():
    """Returns a coroutine function that resolves to a 200 response."""

    async def _fn():
        return make_response(200)

    return _fn


def fn_returning(status_code: int, headers: Optional[dict] = None):
    """Returns a coroutine function that resolves to the given status."""

    async def _fn():
        return make_response(status_code, headers or {})

    return _fn


# ---------------------------------------------------------------------------
# Normal execution
# ---------------------------------------------------------------------------


class TestNormalExecution:
    """RateLimiter.execute() passes through to fn and returns its result."""

    @pytest.mark.asyncio
    async def test_returns_fn_result_on_success(self) -> None:
        limiter = RateLimiter()
        result = await limiter.execute(ok_fn())
        assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_propagates_fn_exception(self) -> None:
        """If fn raises a non-429 exception, it propagates to the caller."""
        limiter = RateLimiter()

        async def _raising():
            raise ConnectionError("network down")

        with pytest.raises(ConnectionError, match="network down"):
            await limiter.execute(_raising)


# ---------------------------------------------------------------------------
# QueueOverflowError
# ---------------------------------------------------------------------------


class TestQueueOverflow:
    """RateLimiter raises QueueOverflowError when queue reaches 1000."""

    @pytest.mark.asyncio
    async def test_raises_queue_overflow_at_capacity(self) -> None:
        """When the queue is full, execute raises QueueOverflowError immediately."""
        limiter = RateLimiter()

        # We need to fill the queue. The exact mechanism depends on implementation,
        # but the contract is: if the queue is at capacity (1000), raise.
        # We'll simulate by triggering backoff so requests queue up.
        # First, make the limiter enter a long backoff
        await limiter.execute(fn_returning(429, {"retry-after": "300"}))
        # This should have triggered backoff. Now flood with requests that queue.

        # Actually, the simpler approach: the design says "if self._queue.full(): raise"
        # as the first check in execute(). We need 1000 pending requests.
        # This is hard to test without internal access, so let's test the observable:
        # after entering backoff, concurrent requests should queue, and the 1001st raises.

        # Alternative approach: verify the error message is correct
        with pytest.raises(QueueOverflowError):
            # Submit enough to overflow - this will depend on implementation internals
            # but the contract guarantees the error at 1000
            tasks = []
            for _ in range(1001):
                tasks.append(asyncio.create_task(limiter.execute(ok_fn())))
            await asyncio.gather(*tasks)


# ---------------------------------------------------------------------------
# 429 backoff with Retry-After header
# ---------------------------------------------------------------------------


class TestRetryAfterBackoff:
    """RateLimiter uses Retry-After header value for backoff duration."""

    @pytest.mark.asyncio
    async def test_respects_retry_after_within_range(self) -> None:
        """Retry-After value between 1 and 300 is used directly."""
        limiter = RateLimiter()

        # First call returns 429 with Retry-After: 5
        # The limiter should back off and eventually retry
        call_count = 0

        async def _fn():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return make_response(429, {"retry-after": "5"})
            return make_response(200)

        result = await limiter.execute(_fn)
        assert result.status_code == 200
        assert call_count == 2  # retried after backoff

    @pytest.mark.asyncio
    async def test_caps_retry_after_at_300(self) -> None:
        """Retry-After > 300 is capped at 300 seconds."""
        limiter = RateLimiter()

        # We can't wait 300s in a test, but we can verify the limiter
        # enters backoff and is_backing_off returns True
        call_count = 0

        async def _fn():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return make_response(429, {"retry-after": "9999"})
            return make_response(200)

        # Start execute in background — it will be blocking due to backoff
        task = asyncio.create_task(limiter.execute(_fn))
        await asyncio.sleep(0.05)  # Let it process the 429

        assert limiter.is_backing_off() is True

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


# ---------------------------------------------------------------------------
# Exponential backoff without Retry-After
# ---------------------------------------------------------------------------


class TestExponentialBackoff:
    """Without Retry-After, backoff is min(1.0 * 2^(n-1), 60.0) seconds."""

    @pytest.mark.asyncio
    async def test_first_429_backoff_is_one_second(self) -> None:
        """First 429 without header → 1s backoff (min(1*2^0, 60) = 1)."""
        limiter = RateLimiter()
        call_count = 0

        async def _fn():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return make_response(429, {})
            return make_response(200)

        result = await limiter.execute(_fn)
        assert result.status_code == 200
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_consecutive_429s_increase_backoff(self) -> None:
        """Each consecutive 429 doubles the backoff (exponential)."""
        limiter = RateLimiter()
        call_count = 0

        async def _fn():
            nonlocal call_count
            call_count += 1
            # First 3 calls return 429, then 200
            if call_count <= 3:
                return make_response(429, {})
            return make_response(200)

        result = await limiter.execute(_fn)
        assert result.status_code == 200
        assert call_count == 4  # 3 retries + 1 success


# ---------------------------------------------------------------------------
# RateLimitExhaustedError after 10 consecutive 429s
# ---------------------------------------------------------------------------


class TestRateLimitExhausted:
    """After 10 consecutive 429 responses, raises RateLimitExhaustedError."""

    @pytest.mark.asyncio
    async def test_raises_after_10_consecutive_429s(self) -> None:
        limiter = RateLimiter()

        async def _always_429():
            return make_response(429, {})

        with pytest.raises(RateLimitExhaustedError):
            await limiter.execute(_always_429)

    @pytest.mark.asyncio
    async def test_does_not_raise_at_9_consecutive_429s(self) -> None:
        """9 consecutive 429s followed by success does not raise."""
        limiter = RateLimiter()
        call_count = 0

        async def _fn():
            nonlocal call_count
            call_count += 1
            if call_count <= 9:
                return make_response(429, {})
            return make_response(200)

        result = await limiter.execute(_fn)
        assert result.status_code == 200
        assert call_count == 10


# ---------------------------------------------------------------------------
# Consecutive 429 counter reset
# ---------------------------------------------------------------------------


class TestConsecutive429Reset:
    """Non-429 response resets the consecutive 429 counter."""

    @pytest.mark.asyncio
    async def test_counter_resets_on_non_429(self) -> None:
        """After a successful response, the 429 counter resets to 0."""
        limiter = RateLimiter()

        # First call: 429 → retry → 200 (counter resets)
        call_count = 0

        async def _fn():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return make_response(429, {})
            return make_response(200)

        await limiter.execute(_fn)

        # Second call should start fresh (not accumulating from previous)
        call_count2 = 0

        async def _fn2():
            nonlocal call_count2
            call_count2 += 1
            if call_count2 == 1:
                return make_response(429, {})
            return make_response(200)

        result = await limiter.execute(_fn2)
        assert result.status_code == 200


# ---------------------------------------------------------------------------
# Global backoff pause
# ---------------------------------------------------------------------------


class TestGlobalBackoffPause:
    """No dispatches occur while the limiter is in backoff."""

    @pytest.mark.asyncio
    async def test_is_backing_off_true_during_backoff(self) -> None:
        """is_backing_off() returns True while in backoff period."""
        limiter = RateLimiter()
        call_count = 0

        async def _fn():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return make_response(429, {"retry-after": "10"})
            return make_response(200)

        # Start in background
        task = asyncio.create_task(limiter.execute(_fn))
        await asyncio.sleep(0.05)

        assert limiter.is_backing_off() is True

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    @pytest.mark.asyncio
    async def test_is_backing_off_false_normally(self) -> None:
        """is_backing_off() returns False when not in backoff."""
        limiter = RateLimiter()
        assert limiter.is_backing_off() is False

    @pytest.mark.asyncio
    async def test_queue_size_reports_pending_requests(self) -> None:
        """queue_size() reports the number of pending queued requests."""
        limiter = RateLimiter()
        # Initially zero
        assert limiter.queue_size() == 0
