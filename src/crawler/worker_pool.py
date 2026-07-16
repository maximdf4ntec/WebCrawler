"""Worker Pool — bounded concurrency via asyncio.Semaphore (Task 6.1).

Manages a pool of concurrent workers. Each worker processes a single URL
(LeaseResult) to completion. The pool enforces a configurable maximum
concurrency between 1 and 100.
"""

import asyncio
from typing import Awaitable, Callable, Optional

from crawler.types import LeaseResult


class WorkerPool:
    """Manages a bounded pool of concurrent workers using asyncio.Semaphore."""

    def __init__(
        self,
        max_concurrency: int,
        worker_fn: Optional[Callable[[LeaseResult], Awaitable[None]]] = None,
    ) -> None:
        if not (1 <= max_concurrency <= 100):
            raise ValueError("max_concurrency must be between 1 and 100")

        self._max_concurrency = max_concurrency
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._worker_fn = worker_fn
        self._active_count = 0
        self._tasks: set[asyncio.Task] = set()
        # Event signalled whenever a worker finishes and frees a slot.
        # More efficient than asyncio.wait(tasks) for slot notification.
        self._slot_available = asyncio.Event()
        self._slot_available.set()  # Initially all slots are free

    async def dispatch(self, lease: LeaseResult) -> None:
        """Submit a lease for worker processing, bounded by the concurrency limit.

        Blocks the caller until a semaphore slot is available, then launches
        an asyncio.Task and returns. Callers that need non-blocking submission
        should check ``has_capacity()`` or call ``wait_for_slot()`` beforehand.
        """
        await self._semaphore.acquire()
        self._active_count += 1
        if self._active_count >= self._max_concurrency:
            self._slot_available.clear()
        task = asyncio.create_task(self._run_worker(lease))
        self._tasks.add(task)
        task.add_done_callback(self._task_done)

    def has_capacity(self) -> bool:
        """Return True if active_count < max_concurrency."""
        return self._active_count < self._max_concurrency

    async def wait_for_slot(self) -> None:
        """Block until at least one concurrency slot is available."""
        while not self.has_capacity():
            self._slot_available.clear()
            await self._slot_available.wait()

    def active_count(self) -> int:
        """Return the number of currently executing workers."""
        return self._active_count

    async def drain(self) -> None:
        """Wait for all in-flight workers to complete."""
        if self._tasks:
            # gather() captures task references at call time, so concurrent
            # _task_done callbacks discarding from self._tasks are safe here.
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _run_worker(self, lease: LeaseResult) -> None:
        """Execute the worker function for a single lease, releasing the semaphore on completion."""
        try:
            if self._worker_fn is not None:
                await self._worker_fn(lease)
        finally:
            self._active_count -= 1
            self._semaphore.release()
            self._slot_available.set()

    def _task_done(self, task: asyncio.Task) -> None:
        """Callback to remove completed tasks from the tracking set."""
        self._tasks.discard(task)
