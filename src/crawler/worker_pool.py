"""Worker Pool — manages a bounded pool of concurrent workers using asyncio.

Provides dispatch, capacity tracking, and graceful drain functionality.
The pool enforces max_concurrency via an asyncio.Semaphore and tracks
active workers for coordination with the Scheduler.

Requirements: 4.1 (bounded concurrency), 17.3 (graceful drain)
"""

import asyncio
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
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._active_count = 0
        self._active_lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self._slot_available = asyncio.Event()
        self._slot_available.set()  # Initially has capacity

    async def dispatch(self, lease: LeaseResult) -> None:
        """Submit a lease for worker processing (bounded by concurrency limit).

        Acquires a semaphore slot (blocks if at capacity), then spawns
        a background task to execute worker_fn with the lease.
        """
        await self._semaphore.acquire()
        async with self._active_lock:
            self._active_count += 1
            if self._active_count >= self._max_concurrency:
                self._slot_available.clear()

        task = asyncio.create_task(self._run_worker(lease))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run_worker(self, lease: LeaseResult) -> None:
        """Execute worker_fn and release the semaphore slot on completion."""
        try:
            await self._worker_fn(lease)
        finally:
            self._semaphore.release()
            async with self._active_lock:
                self._active_count -= 1
                self._slot_available.set()

    def has_capacity(self) -> bool:
        """Return True if active_count < max_concurrency."""
        return self._active_count < self._max_concurrency

    async def wait_for_slot(self) -> None:
        """Block until at least one slot is available."""
        while not self.has_capacity():
            self._slot_available.clear()
            await self._slot_available.wait()

    def active_count(self) -> int:
        """Return the number of currently executing workers."""
        return self._active_count

    async def drain(self) -> None:
        """Wait for all in-flight workers to complete."""
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
