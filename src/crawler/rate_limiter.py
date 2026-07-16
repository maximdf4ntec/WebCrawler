# Stub — implementation pending (Task 5.1)
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")


class RateLimiter:
    """Centralized token-bucket rate limiter with 429 backoff handling."""

    async def execute(self, fn: Callable[[], Awaitable[T]]) -> T:
        """Execute fn with rate limiting. Raises on queue overflow or 429 exhaustion."""
        raise NotImplementedError

    def queue_size(self) -> int:
        """Return current queue depth."""
        raise NotImplementedError

    def is_backing_off(self) -> bool:
        """Return True if currently in a backoff period."""
        raise NotImplementedError
