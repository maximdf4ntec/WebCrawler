"""
Unit tests for Scheduler (Task 9.1).

Tests:
- init() enqueues seed URL at depth 0
- run() acquires leases and dispatches to worker pool
- run() terminates when frontier is exhausted (no Pending/Retry/In_Progress)
- run() waits for slot when worker pool is full
- run() caps lease batch by min(available_slots, batch_size)
- run() sleeps and retries when no batch but work is still pending
- shutdown() stops dispatch loop and drains worker pool
- Retry scheduling uses exponential backoff: min(1.0 * 2^(n-1), 300.0)

All collaborators (metadata_store, worker_pool) are mocked.
"""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

from crawler.scheduler import Scheduler
from crawler.types import CrawlerConfig, LeaseResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> CrawlerConfig:
    defaults = {
        "seed_url": "https://example.com",
        "max_concurrency": 5,
        "max_retries": 3,
        "batch_size": 10,
        "lease_timeout_ms": 60000,
    }
    defaults.update(overrides)
    return CrawlerConfig(**defaults)


def _make_lease(url: str = "https://example.com/page", depth: int = 0) -> LeaseResult:
    return LeaseResult(
        normalized_url=url,
        url=url,
        depth=depth,
        lease_token="token-123",
        lease_expires_at=9999999999999,
    )


def _mock_store(
    leases: list[LeaseResult] | None = None,
    state_counts: dict | None = None,
) -> AsyncMock:
    """Create a mock MetadataStore."""
    store = AsyncMock()
    store.enqueue = AsyncMock()
    store.acquire_lease_batch = AsyncMock(return_value=leases or [])
    store.get_state_counts = AsyncMock(
        return_value=state_counts
        or {
            "Pending": 0,
            "Retry": 0,
            "In_Progress": 0,
            "Completed": 0,
            "Failed": 0,
            "Terminal_Failed": 0,
        }
    )
    store.expire_leases = AsyncMock(return_value=0)
    return store


def _mock_worker_pool(active: int = 0, max_conc: int = 5) -> Mock:
    """Create a mock WorkerPool."""
    pool = Mock()
    pool.active_count = Mock(return_value=active)
    pool.has_capacity = Mock(return_value=active < max_conc)
    pool.wait_for_slot = AsyncMock()
    pool.dispatch = AsyncMock()
    pool.drain = AsyncMock()
    return pool


# ---------------------------------------------------------------------------
# init() — Seeds the queue
# ---------------------------------------------------------------------------


class TestInit:
    """Scheduler.init() bootstraps the crawl."""

    @pytest.mark.asyncio
    async def test_init_enqueues_seed_url_at_depth_zero(self) -> None:
        """The seed URL is enqueued at depth 0 during init."""
        config = _make_config(seed_url="https://example.com")
        store = _mock_store()

        scheduler = Scheduler()
        await scheduler.init(config, store)

        store.enqueue.assert_called_once()
        call_args = store.enqueue.call_args
        # Verify the normalized URL contains the seed domain
        assert "example.com" in str(call_args)
        # Verify depth=0 is passed (positional or keyword)
        all_args = str(call_args)
        assert "0" in all_args or "depth=0" in all_args


# ---------------------------------------------------------------------------
# run() — Main loop dispatch
# ---------------------------------------------------------------------------


class TestRunDispatch:
    """Scheduler.run() acquires leases and dispatches to workers."""

    @pytest.mark.asyncio
    async def test_run_dispatches_leased_urls_to_worker_pool(self) -> None:
        """Leased URLs are dispatched to the worker pool."""
        config = _make_config()
        leases = [_make_lease(f"https://example.com/{i}") for i in range(3)]

        # Store returns leases on first call, then empty (triggering termination)
        store = _mock_store()
        call_count = 0

        async def _acquire_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return leases
            return []

        store.acquire_lease_batch = AsyncMock(side_effect=_acquire_side_effect)
        # After first batch, frontier is empty → terminate
        store.get_state_counts = AsyncMock(
            return_value={"Pending": 0, "Retry": 0, "In_Progress": 0}
        )

        pool = _mock_worker_pool()
        scheduler = Scheduler()
        await scheduler.init(config, store)
        scheduler._worker_pool = pool  # inject mock

        await scheduler.run()

        assert pool.dispatch.call_count == 3

    @pytest.mark.asyncio
    async def test_run_caps_batch_by_available_slots(self) -> None:
        """Lease batch size is min(available_slots, config.batch_size)."""
        config = _make_config(max_concurrency=5, batch_size=50)
        store = _mock_store()

        # Worker pool has 3 active → 2 available slots
        pool = _mock_worker_pool(active=3, max_conc=5)

        # Store returns empty to terminate
        store.acquire_lease_batch = AsyncMock(return_value=[])
        store.get_state_counts = AsyncMock(
            return_value={"Pending": 0, "Retry": 0, "In_Progress": 0}
        )

        scheduler = Scheduler()
        await scheduler.init(config, store)
        scheduler._worker_pool = pool

        await scheduler.run()

        # acquire_lease_batch MUST have been called
        assert store.acquire_lease_batch.called, "acquire_lease_batch was never called"
        call_args = store.acquire_lease_batch.call_args
        batch_size_arg = (
            call_args[0][0] if call_args[0] else call_args[1].get("batch_size")
        )
        # available_slots = 5 - 3 = 2, batch_size = 50 → capped at 2
        assert batch_size_arg <= 2


# ---------------------------------------------------------------------------
# run() — Terminal condition
# ---------------------------------------------------------------------------


class TestRunTermination:
    """Scheduler.run() terminates when no processable URLs remain."""

    @pytest.mark.asyncio
    async def test_run_terminates_when_frontier_exhausted(self) -> None:
        """No Pending, Retry, or In_Progress → crawl complete, run() returns."""
        config = _make_config()
        store = _mock_store(
            leases=[],
            state_counts={"Pending": 0, "Retry": 0, "In_Progress": 0},
        )
        pool = _mock_worker_pool()

        scheduler = Scheduler()
        await scheduler.init(config, store)
        scheduler._worker_pool = pool

        # run() should return (not hang)
        await asyncio.wait_for(scheduler.run(), timeout=2.0)

    @pytest.mark.asyncio
    async def test_run_does_not_terminate_when_workers_still_active(self) -> None:
        """In_Progress > 0 means workers are running → don't terminate yet."""
        config = _make_config()
        store = _mock_store()

        call_count = 0

        async def _counts_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                # Workers still active
                return {"Pending": 0, "Retry": 0, "In_Progress": 1}
            # All done
            return {"Pending": 0, "Retry": 0, "In_Progress": 0}

        store.acquire_lease_batch = AsyncMock(return_value=[])
        store.get_state_counts = AsyncMock(side_effect=_counts_side_effect)
        pool = _mock_worker_pool()

        scheduler = Scheduler()
        await scheduler.init(config, store)
        scheduler._worker_pool = pool

        await asyncio.wait_for(scheduler.run(), timeout=3.0)

        # Should have polled multiple times before terminating
        assert store.get_state_counts.call_count >= 2


# ---------------------------------------------------------------------------
# run() — Wait for slot
# ---------------------------------------------------------------------------


class TestRunWaitForSlot:
    """Scheduler.run() waits when worker pool is at capacity."""

    @pytest.mark.asyncio
    async def test_run_waits_for_slot_when_pool_full(self) -> None:
        """When no available slots, scheduler calls wait_for_slot."""
        config = _make_config(max_concurrency=2)
        store = _mock_store()

        # Pool is full initially, then has capacity
        call_count = 0

        def _active_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return 2  # full
            return 0  # slots available

        pool = _mock_worker_pool()
        pool.active_count = Mock(side_effect=_active_side_effect)

        store.acquire_lease_batch = AsyncMock(return_value=[])
        store.get_state_counts = AsyncMock(
            return_value={"Pending": 0, "Retry": 0, "In_Progress": 0}
        )

        scheduler = Scheduler()
        await scheduler.init(config, store)
        scheduler._worker_pool = pool

        await asyncio.wait_for(scheduler.run(), timeout=2.0)

        pool.wait_for_slot.assert_called()


# ---------------------------------------------------------------------------
# shutdown()
# ---------------------------------------------------------------------------


class TestShutdown:
    """Scheduler.shutdown() stops dispatching and drains workers."""

    @pytest.mark.asyncio
    async def test_shutdown_drains_worker_pool(self) -> None:
        """shutdown() calls drain on the worker pool."""
        config = _make_config()
        store = _mock_store()
        pool = _mock_worker_pool()

        scheduler = Scheduler()
        await scheduler.init(config, store)
        scheduler._worker_pool = pool

        await scheduler.shutdown()

        pool.drain.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown_stops_run_loop(self) -> None:
        """After shutdown(), run() exits even if frontier has URLs."""
        config = _make_config()
        store = _mock_store()
        # Frontier always has pending URLs — run would loop forever without shutdown
        store.acquire_lease_batch = AsyncMock(return_value=[_make_lease()])
        store.get_state_counts = AsyncMock(
            return_value={"Pending": 5, "Retry": 0, "In_Progress": 0}
        )
        pool = _mock_worker_pool()

        scheduler = Scheduler()
        await scheduler.init(config, store)
        scheduler._worker_pool = pool

        # Start run in background, then shutdown
        run_task = asyncio.create_task(scheduler.run())
        await asyncio.sleep(0.1)
        await scheduler.shutdown()

        # run should complete within timeout
        await asyncio.wait_for(run_task, timeout=2.0)


# ---------------------------------------------------------------------------
# Retry backoff formula
# ---------------------------------------------------------------------------


class TestRetryBackoff:
    """Scheduler retry scheduling uses min(1.0 * 2^(n-1), 300.0) seconds."""

    @pytest.mark.asyncio
    async def test_retry_backoff_attempt_1_is_one_second(self) -> None:
        """On first retry, scheduler computes next_retry_at = now + 1000ms."""
        config = _make_config(max_retries=3)
        store = _mock_store()
        pool = _mock_worker_pool()

        scheduler = Scheduler()
        await scheduler.init(config, store)
        scheduler._worker_pool = pool

        # The scheduler must expose a backoff computation method or we verify
        # it calls mark_retry with the correct delay. Since it's a stub,
        # we test the contract: compute_backoff(n) returns expected values.
        # The scheduler MUST implement this formula.
        backoff = scheduler.compute_retry_delay(attempt=1)
        assert backoff == 1.0

    @pytest.mark.asyncio
    async def test_retry_backoff_attempt_2_is_two_seconds(self) -> None:
        """On second retry, backoff = 2.0 seconds."""
        config = _make_config()
        store = _mock_store()

        scheduler = Scheduler()
        await scheduler.init(config, store)

        backoff = scheduler.compute_retry_delay(attempt=2)
        assert backoff == 2.0

    @pytest.mark.asyncio
    async def test_retry_backoff_attempt_3_is_four_seconds(self) -> None:
        """On third retry, backoff = 4.0 seconds."""
        config = _make_config()
        store = _mock_store()

        scheduler = Scheduler()
        await scheduler.init(config, store)

        backoff = scheduler.compute_retry_delay(attempt=3)
        assert backoff == 4.0

    @pytest.mark.asyncio
    async def test_retry_backoff_capped_at_300_seconds(self) -> None:
        """Large attempt numbers cap at 300 seconds."""
        config = _make_config()
        store = _mock_store()

        scheduler = Scheduler()
        await scheduler.init(config, store)

        backoff = scheduler.compute_retry_delay(attempt=10)
        assert backoff == 300.0


# ---------------------------------------------------------------------------
# expire_leases() — Lease expiration detection
# ---------------------------------------------------------------------------


class TestLeaseExpiration:
    """Scheduler calls expire_leases() during its loop for lease recovery."""

    @pytest.mark.asyncio
    async def test_run_calls_expire_leases(self) -> None:
        """Scheduler calls store.expire_leases() during the crawl loop."""
        config = _make_config()
        store = _mock_store(
            leases=[],
            state_counts={"Pending": 0, "Retry": 0, "In_Progress": 0},
        )
        pool = _mock_worker_pool()

        scheduler = Scheduler()
        await scheduler.init(config, store)
        scheduler._worker_pool = pool

        await asyncio.wait_for(scheduler.run(), timeout=2.0)

        store.expire_leases.assert_called()
