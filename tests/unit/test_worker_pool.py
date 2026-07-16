"""
Unit tests for WorkerPool (Task 6.1).

Tests:
- Bounded concurrency: never exceeds max_concurrency simultaneous workers
- dispatch() submits work and invokes worker_fn with the lease
- has_capacity() reflects available slots accurately
- active_count() tracks in-flight workers (0 initially, increments, decrements)
- wait_for_slot() blocks when full, unblocks when slot frees
- drain() waits for all workers to finish, active_count == 0 after

Uses asyncio.Event for controlled worker duration so tests can observe
concurrency state during execution.
"""

import asyncio

import pytest

from crawler.worker_pool import WorkerPool
from crawler.types import LeaseResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_lease(url: str = "https://example.com/page", depth: int = 0) -> LeaseResult:
    """Create a test LeaseResult."""
    return LeaseResult(
        normalized_url=url,
        url=url,
        depth=depth,
        lease_token="test-token-123",
        lease_expires_at=9999999999999,
    )


# ---------------------------------------------------------------------------
# Bounded concurrency
# ---------------------------------------------------------------------------


class TestBoundedConcurrency:
    """WorkerPool never exceeds max_concurrency simultaneous workers."""

    @pytest.mark.asyncio
    async def test_concurrent_workers_never_exceed_max(self) -> None:
        """With max_concurrency=2, peak active count never exceeds 2."""
        peak_concurrent = 0
        current_concurrent = 0
        gate = asyncio.Event()

        async def slow_worker(lease: LeaseResult) -> None:
            nonlocal peak_concurrent, current_concurrent
            current_concurrent += 1
            peak_concurrent = max(peak_concurrent, current_concurrent)
            await gate.wait()
            current_concurrent -= 1

        pool = WorkerPool(max_concurrency=2, worker_fn=slow_worker)

        # Dispatch 4 workers — only 2 should run concurrently
        for i in range(4):
            asyncio.create_task(pool.dispatch(_make_lease(f"https://example.com/{i}")))

        await asyncio.sleep(0.05)  # Let dispatches start

        # At this point peak should be exactly 2
        assert peak_concurrent <= 2
        assert pool.active_count() <= 2

        # Release all workers
        gate.set()
        await asyncio.sleep(0.05)
        await pool.drain()

        assert peak_concurrent == 2

    @pytest.mark.asyncio
    async def test_max_concurrency_of_one_serializes_workers(self) -> None:
        """With max_concurrency=1, workers run one at a time."""
        peak = 0
        current = 0
        gate = asyncio.Event()

        async def slow_worker(lease: LeaseResult) -> None:
            nonlocal peak, current
            current += 1
            peak = max(peak, current)
            await gate.wait()
            current -= 1

        pool = WorkerPool(max_concurrency=1, worker_fn=slow_worker)

        for i in range(3):
            asyncio.create_task(pool.dispatch(_make_lease(f"https://example.com/{i}")))

        await asyncio.sleep(0.05)
        assert peak == 1
        assert pool.active_count() == 1

        gate.set()
        await pool.drain()


# ---------------------------------------------------------------------------
# dispatch() — invokes worker_fn
# ---------------------------------------------------------------------------


class TestDispatch:
    """WorkerPool.dispatch() invokes worker_fn with the dispatched lease."""

    @pytest.mark.asyncio
    async def test_dispatch_invokes_worker_fn_with_lease(self) -> None:
        """worker_fn is called with the dispatched LeaseResult."""
        received_leases: list[LeaseResult] = []

        async def worker(lease: LeaseResult) -> None:
            received_leases.append(lease)

        pool = WorkerPool(max_concurrency=5, worker_fn=worker)
        lease = _make_lease("https://example.com/target")

        await pool.dispatch(lease)
        await pool.drain()

        assert len(received_leases) == 1
        assert received_leases[0].normalized_url == "https://example.com/target"

    @pytest.mark.asyncio
    async def test_dispatch_processes_multiple_leases(self) -> None:
        """Multiple dispatches each invoke worker_fn."""
        call_count = 0

        async def worker(lease: LeaseResult) -> None:
            nonlocal call_count
            call_count += 1

        pool = WorkerPool(max_concurrency=5, worker_fn=worker)
        for i in range(5):
            await pool.dispatch(_make_lease(f"https://example.com/{i}"))

        await pool.drain()
        assert call_count == 5


# ---------------------------------------------------------------------------
# has_capacity()
# ---------------------------------------------------------------------------


class TestHasCapacity:
    """WorkerPool.has_capacity() returns True when slots are available."""

    @pytest.mark.asyncio
    async def test_has_capacity_true_initially(self) -> None:
        """Fresh pool has capacity."""

        async def noop(lease: LeaseResult) -> None:
            pass

        pool = WorkerPool(max_concurrency=5, worker_fn=noop)
        assert pool.has_capacity() is True

    @pytest.mark.asyncio
    async def test_has_capacity_false_when_full(self) -> None:
        """Pool reports no capacity when max workers are in-flight."""
        gate = asyncio.Event()

        async def slow_worker(lease: LeaseResult) -> None:
            await gate.wait()

        pool = WorkerPool(max_concurrency=2, worker_fn=slow_worker)

        # Fill the pool
        asyncio.create_task(pool.dispatch(_make_lease("https://example.com/1")))
        asyncio.create_task(pool.dispatch(_make_lease("https://example.com/2")))
        await asyncio.sleep(0.05)

        assert pool.has_capacity() is False

        # Release workers
        gate.set()
        await pool.drain()

    @pytest.mark.asyncio
    async def test_has_capacity_true_after_worker_finishes(self) -> None:
        """Capacity returns after a worker completes."""
        gate = asyncio.Event()

        async def slow_worker(lease: LeaseResult) -> None:
            await gate.wait()

        pool = WorkerPool(max_concurrency=1, worker_fn=slow_worker)
        asyncio.create_task(pool.dispatch(_make_lease()))
        await asyncio.sleep(0.05)

        assert pool.has_capacity() is False

        gate.set()
        await asyncio.sleep(0.05)

        assert pool.has_capacity() is True


# ---------------------------------------------------------------------------
# active_count()
# ---------------------------------------------------------------------------


class TestActiveCount:
    """WorkerPool.active_count() tracks currently executing workers."""

    @pytest.mark.asyncio
    async def test_active_count_starts_at_zero(self) -> None:
        """No workers active initially."""

        async def noop(lease: LeaseResult) -> None:
            pass

        pool = WorkerPool(max_concurrency=5, worker_fn=noop)
        assert pool.active_count() == 0

    @pytest.mark.asyncio
    async def test_active_count_increments_during_processing(self) -> None:
        """active_count > 0 while workers are in-flight."""
        gate = asyncio.Event()

        async def slow_worker(lease: LeaseResult) -> None:
            await gate.wait()

        pool = WorkerPool(max_concurrency=5, worker_fn=slow_worker)
        asyncio.create_task(pool.dispatch(_make_lease()))
        await asyncio.sleep(0.05)

        assert pool.active_count() == 1

        gate.set()
        await pool.drain()

    @pytest.mark.asyncio
    async def test_active_count_decrements_after_worker_completes(self) -> None:
        """active_count drops back to 0 after worker finishes."""
        gate = asyncio.Event()

        async def slow_worker(lease: LeaseResult) -> None:
            await gate.wait()

        pool = WorkerPool(max_concurrency=5, worker_fn=slow_worker)
        asyncio.create_task(pool.dispatch(_make_lease()))
        await asyncio.sleep(0.05)

        assert pool.active_count() == 1

        gate.set()
        await asyncio.sleep(0.05)

        assert pool.active_count() == 0


# ---------------------------------------------------------------------------
# wait_for_slot()
# ---------------------------------------------------------------------------


class TestWaitForSlot:
    """WorkerPool.wait_for_slot() blocks until capacity is available."""

    @pytest.mark.asyncio
    async def test_wait_for_slot_returns_immediately_when_capacity(self) -> None:
        """wait_for_slot returns immediately if there's already capacity."""

        async def noop(lease: LeaseResult) -> None:
            pass

        pool = WorkerPool(max_concurrency=5, worker_fn=noop)
        await asyncio.wait_for(pool.wait_for_slot(), timeout=1.0)

    @pytest.mark.asyncio
    async def test_wait_for_slot_blocks_when_full_then_unblocks(self) -> None:
        """wait_for_slot blocks when full, then unblocks when a worker finishes."""
        gate = asyncio.Event()
        unblocked = asyncio.Event()

        async def slow_worker(lease: LeaseResult) -> None:
            await gate.wait()

        pool = WorkerPool(max_concurrency=1, worker_fn=slow_worker)

        # Fill the pool
        asyncio.create_task(pool.dispatch(_make_lease()))
        await asyncio.sleep(0.05)

        # wait_for_slot should block
        async def _wait_and_signal():
            await pool.wait_for_slot()
            unblocked.set()

        wait_task = asyncio.create_task(_wait_and_signal())
        await asyncio.sleep(0.05)

        # Should still be blocked
        assert not unblocked.is_set()

        # Release the worker → slot opens
        gate.set()
        await asyncio.sleep(0.05)

        # Now it should have unblocked
        assert unblocked.is_set()

        await wait_task
        await pool.drain()


# ---------------------------------------------------------------------------
# drain()
# ---------------------------------------------------------------------------


class TestDrain:
    """WorkerPool.drain() waits for all in-flight workers to complete."""

    @pytest.mark.asyncio
    async def test_drain_with_no_workers_returns_immediately(self) -> None:
        """drain() with no active workers returns immediately."""

        async def noop(lease: LeaseResult) -> None:
            pass

        pool = WorkerPool(max_concurrency=5, worker_fn=noop)
        await asyncio.wait_for(pool.drain(), timeout=1.0)

    @pytest.mark.asyncio
    async def test_drain_waits_for_all_active_workers(self) -> None:
        """After drain(), all workers have finished and active_count is 0."""
        completed = []
        gate = asyncio.Event()

        async def slow_worker(lease: LeaseResult) -> None:
            await gate.wait()
            completed.append(lease.normalized_url)

        pool = WorkerPool(max_concurrency=5, worker_fn=slow_worker)
        for i in range(3):
            asyncio.create_task(pool.dispatch(_make_lease(f"https://example.com/{i}")))

        await asyncio.sleep(0.05)
        assert pool.active_count() == 3

        # Release workers
        gate.set()
        await pool.drain()

        assert pool.active_count() == 0
        assert len(completed) == 3
