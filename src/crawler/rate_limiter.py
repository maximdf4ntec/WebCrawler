"""Centralized rate limiter with asyncio-based FIFO queue and 429 backoff handling.

All Workers submit Fetch API requests through this single gateway.
The rate limiter enforces:
- FIFO ordering of queued requests
- Global backoff pause on 429 responses
- Exponential backoff without Retry-After header
- Queue capacity limit of 1000 pending requests
- RateLimitExhaustedError after 10 consecutive 429s per execute() call

Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8
"""

import asyncio
from typing import Awaitable, Callable, Optional, TypeVar

from crawler.types import QueueOverflowError, RateLimitExhaustedError

T = TypeVar("T")

_MAX_QUEUE_SIZE = 1000
_MAX_CONSECUTIVE_429 = 10
_EXPONENTIAL_BACKOFF_BASE = 1.0
_EXPONENTIAL_BACKOFF_CAP = 60.0
_RETRY_AFTER_CAP = 300.0


class RateLimiter:
    """Centralized rate limiter gateway for all Fetch API requests.

    Implements:
    - asyncio.Semaphore for concurrency gating
    - asyncio.Event for global backoff pause
    - FIFO ordering via asyncio queue semantics
    - Per-execute() 429 retry loop with exhaustion detection
    """

    def __init__(
        self,
        max_concurrency: int = 10,
        *,
        _sleep: Optional[Callable[[float], Awaitable[None]]] = None,
    ) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._backoff_event = asyncio.Event()
        self._backoff_event.set()  # Not backing off initially
        self._pending_count = 0
        self._pending_lock = asyncio.Lock()
        self._sleep = _sleep or asyncio.sleep

    async def execute(self, fn: Callable[[], Awaitable[T]]) -> T:
        """Execute fn through the rate limiter.

        Queues the request if at capacity, dispatches in FIFO order,
        and handles 429 backoff internally with retries.

        Raises:
            QueueOverflowError: If the queue has reached 1000 pending requests.
            RateLimitExhaustedError: After 10 consecutive 429 responses.
        """
        # Check queue capacity
        async with self._pending_lock:
            if self._pending_count >= _MAX_QUEUE_SIZE:
                raise QueueOverflowError(
                    f"Rate limiter queue at capacity ({_MAX_QUEUE_SIZE})"
                )
            self._pending_count += 1

        try:
            return await self._execute_with_retry(fn)
        finally:
            async with self._pending_lock:
                self._pending_count -= 1

    async def _execute_with_retry(self, fn: Callable[[], Awaitable[T]]) -> T:
        """Internal retry loop for a single request handling 429 responses."""
        consecutive_429_count = 0

        while True:
            # Wait for global backoff to clear
            await self._backoff_event.wait()

            # Acquire concurrency slot
            await self._semaphore.acquire()
            try:
                result = await fn()
            except Exception:
                self._semaphore.release()
                raise
            else:
                self._semaphore.release()

            # Check for 429 response
            status_code = getattr(result, "status_code", None)
            if status_code == 429:
                consecutive_429_count += 1
                if consecutive_429_count >= _MAX_CONSECUTIVE_429:
                    raise RateLimitExhaustedError(
                        f"Rate limit exhausted after {_MAX_CONSECUTIVE_429} "
                        f"consecutive 429 responses"
                    )

                # Compute backoff duration
                headers = getattr(result, "headers", {}) or {}
                backoff_duration = self._compute_backoff(
                    headers.get("retry-after"), consecutive_429_count
                )

                # Apply global backoff
                await self._apply_backoff(backoff_duration)
                continue

            # Non-429 response — return it
            return result

    def _compute_backoff(
        self, retry_after: Optional[str], consecutive_count: int
    ) -> float:
        """Compute backoff duration from Retry-After header or exponential formula.

        Args:
            retry_after: Value of the Retry-After header, if present.
            consecutive_count: Number of consecutive 429 responses so far.

        Returns:
            Backoff duration in seconds.
        """
        if retry_after is not None:
            try:
                value = float(retry_after)
                if value > _RETRY_AFTER_CAP:
                    return _RETRY_AFTER_CAP
                if value >= 1:
                    return value
            except (ValueError, TypeError):
                pass

        # Exponential backoff: min(1.0 * 2^(n-1), 60.0)
        return min(
            _EXPONENTIAL_BACKOFF_BASE * (2 ** (consecutive_count - 1)),
            _EXPONENTIAL_BACKOFF_CAP,
        )

    async def _apply_backoff(self, duration: float) -> None:
        """Pause all dispatches for the given duration."""
        self._backoff_event.clear()
        try:
            await self._sleep(duration)
        finally:
            self._backoff_event.set()

    def queue_size(self) -> int:
        """Return the current number of pending requests in the queue."""
        return self._pending_count

    def is_backing_off(self) -> bool:
        """Return True if the limiter is currently in a backoff period."""
        return not self._backoff_event.is_set()
