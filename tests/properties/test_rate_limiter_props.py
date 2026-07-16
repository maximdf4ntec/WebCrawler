"""
Property-based tests for RateLimiter.
Feature: web-crawler
Properties: 8 (Backoff Computation), 9 (FIFO Ordering)
"""

import asyncio
from dataclasses import dataclass
from typing import Optional

from hypothesis import given, settings, strategies as st
import pytest

from crawler.rate_limiter import RateLimiter
from crawler.types import RateLimitExhaustedError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class MockResponse:
    status_code: int
    headers: dict[str, str]


async def _instant_sleep(duration: float) -> None:
    """No-op sleep replacement that records the requested duration without waiting."""
    # Yield control to allow event loop to run other tasks (preserves FIFO semantics)
    await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# Property 8: Rate Limiter Backoff Computation
# ---------------------------------------------------------------------------


class TestProperty8_BackoffComputation:
    """Feature: web-crawler, Property 8: Rate Limiter Backoff Computation

    For any 429 with Retry-After v: if 1 ≤ v ≤ 300, backoff = v seconds;
    if v > 300, backoff capped at 300s.
    For any consecutive 429 count n without Retry-After:
    backoff = min(1.0 * 2^(n-1), 60.0) seconds, monotonically non-decreasing.
    """

    @given(retry_after=st.integers(min_value=1, max_value=300))
    @settings(max_examples=100, deadline=None)
    def test_retry_after_within_range_used_directly(self, retry_after: int) -> None:
        """Retry-After in [1, 300] → backoff equals that value in seconds."""

        async def _run():
            observed_backoffs: list[float] = []

            async def _tracking_sleep(duration: float) -> None:
                observed_backoffs.append(duration)
                await asyncio.sleep(0)

            limiter = RateLimiter(_sleep=_tracking_sleep)
            call_count = 0

            async def _fn():
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return MockResponse(429, {"retry-after": str(retry_after)})
                return MockResponse(200, {})

            result = await limiter.execute(_fn)
            assert result.status_code == 200
            assert call_count == 2
            # Verify the backoff duration equals the retry-after value
            assert len(observed_backoffs) == 1
            assert observed_backoffs[0] == float(retry_after)

        asyncio.run(_run())

    @given(retry_after=st.integers(min_value=301, max_value=10000))
    @settings(max_examples=50, deadline=None)
    def test_retry_after_above_300_capped(self, retry_after: int) -> None:
        """Retry-After > 300 → backoff capped at 300 seconds."""

        async def _run():
            observed_backoffs: list[float] = []

            async def _tracking_sleep(duration: float) -> None:
                observed_backoffs.append(duration)
                await asyncio.sleep(0)

            limiter = RateLimiter(_sleep=_tracking_sleep)
            call_count = 0

            async def _fn():
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return MockResponse(429, {"retry-after": str(retry_after)})
                return MockResponse(200, {})

            result = await limiter.execute(_fn)
            assert result.status_code == 200
            # Verify backoff was capped at 300
            assert len(observed_backoffs) == 1
            assert observed_backoffs[0] == 300.0

        asyncio.run(_run())

    @given(n=st.integers(min_value=1, max_value=9))
    @settings(max_examples=50, deadline=None)
    def test_exponential_backoff_without_header_formula(self, n: int) -> None:
        """Without Retry-After, n-th consecutive 429 → min(1.0 * 2^(n-1), 60.0)s.

        We verify the formula directly: for each consecutive 429, the observed
        backoff must equal min(1.0 * 2^(i-1), 60.0) for i=1..n and be
        monotonically non-decreasing.
        """

        async def _run():
            observed_backoffs: list[float] = []

            async def _tracking_sleep(duration: float) -> None:
                observed_backoffs.append(duration)
                await asyncio.sleep(0)

            limiter = RateLimiter(_sleep=_tracking_sleep)
            call_count = 0

            async def _fn():
                nonlocal call_count
                call_count += 1
                if call_count <= n:
                    return MockResponse(429, {})
                return MockResponse(200, {})

            result = await limiter.execute(_fn)
            assert result.status_code == 200
            assert call_count == n + 1

            # Verify each backoff matches the exponential formula
            assert len(observed_backoffs) == n
            for i, backoff in enumerate(observed_backoffs, start=1):
                expected = min(1.0 * 2 ** (i - 1), 60.0)
                assert (
                    backoff == expected
                ), f"Backoff at step {i}: expected {expected}, got {backoff}"

            # Verify monotonically non-decreasing
            for i in range(1, len(observed_backoffs)):
                assert observed_backoffs[i] >= observed_backoffs[i - 1]

        asyncio.run(_run())

    @given(n=st.integers(min_value=10, max_value=15))
    @settings(max_examples=20, deadline=None)
    def test_exhaustion_at_10_or_more_consecutive(self, n: int) -> None:
        """At 10+ consecutive 429s, RateLimitExhaustedError is raised."""

        async def _run():
            limiter = RateLimiter(_sleep=_instant_sleep)

            async def _always_429():
                return MockResponse(429, {})

            with pytest.raises(RateLimitExhaustedError):
                await limiter.execute(_always_429)

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# Property 9: Rate Limiter FIFO Ordering
# ---------------------------------------------------------------------------


class TestProperty9_FIFOOrdering:
    """Feature: web-crawler, Property 9: Rate Limiter FIFO Ordering

    For any sequence of requests submitted while at capacity, queued requests
    SHALL be dispatched in the same order they were submitted (FIFO).
    """

    @given(num_requests=st.integers(min_value=2, max_value=10))
    @settings(max_examples=30, deadline=None)
    def test_requests_dispatched_in_submission_order(self, num_requests: int) -> None:
        """Concurrent requests are dispatched FIFO."""

        async def _run():
            limiter = RateLimiter(_sleep=_instant_sleep)
            execution_order: list[int] = []

            # Trigger a brief backoff so requests queue up
            first_call = True

            async def _trigger_backoff():
                nonlocal first_call
                if first_call:
                    first_call = False
                    return MockResponse(429, {"retry-after": "1"})
                return MockResponse(200, {})

            # Start the backoff trigger
            trigger_task = asyncio.create_task(limiter.execute(_trigger_backoff))
            await asyncio.sleep(0)  # Let backoff take effect

            # Queue up numbered requests
            async def _make_fn(idx: int):
                async def _fn():
                    execution_order.append(idx)
                    return MockResponse(200, {})

                return await limiter.execute(_fn)

            tasks = []
            for i in range(num_requests):
                tasks.append(asyncio.create_task(_make_fn(i)))
                await asyncio.sleep(0)  # Stagger submission slightly

            # Wait for all
            await trigger_task
            await asyncio.gather(*tasks, return_exceptions=True)

            # Verify FIFO: execution order matches submission order
            assert execution_order == list(range(num_requests)), (
                f"FIFO violated: submitted 0..{num_requests-1}, "
                f"executed in order {execution_order}"
            )

        asyncio.run(_run())
