# Stub — implementation pending (Task 6.1)
from typing import Awaitable, Callable

from crawler.types import LeaseResult


class WorkerPool:
    """Manages a bounded pool of concurrent workers using asyncio.Semaphore.

    Args:
        max_concurrency: Maximum number of concurrent workers (1–100).
        worker_fn: Async callable invoked for each dispatched lease.
    """

    def __init__(
        self,
        max_concurrency: int,
        worker_fn: Callable[[LeaseResult], Awaitable[None]],
    ) -> None:
        self._max_concurrency = max_concurrency
        self._worker_fn = worker_fn

    async def dispatch(self, lease: LeaseResult) -> None:
        """Submit a lease for worker processing (bounded by concurrency limit)."""
        raise NotImplementedError

    def has_capacity(self) -> bool:
        """Return True if active_count < max_concurrency."""
        raise NotImplementedError

    async def wait_for_slot(self) -> None:
        """Block until at least one slot is available."""
        raise NotImplementedError

    def active_count(self) -> int:
        """Return the number of currently executing workers."""
        raise NotImplementedError

    async def drain(self) -> None:
        """Wait for all in-flight workers to complete."""
        raise NotImplementedError
