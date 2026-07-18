"""
Unit tests for Worker lease heartbeat (_lease_heartbeat) — Gap #3.

Verifies the background heartbeat task behavior:
- Fires at the correct interval (50% of lease_timeout_ms)
- Stops after max_renewals (3) successful renewals
- Stops early if renew_lease returns False (lease stolen)
- Gets cleanly cancelled when process_url finishes
- Does not interfere with worker processing

Uses asyncio time manipulation to avoid real sleeps.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from crawler.worker import Worker
from crawler.types import (
    CrawlerConfig,
    FetchResponse,
    LeaseResult,
    ProcessorResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(lease_timeout_ms: int = 1000) -> CrawlerConfig:
    """Short lease timeout for fast heartbeat tests."""
    return CrawlerConfig(
        seed_url="https://example.com",
        max_content_size=1024 * 1024,
        max_redirects=5,
        lease_timeout_ms=lease_timeout_ms,
    )


def _make_lease() -> LeaseResult:
    return LeaseResult(
        normalized_url="https://example.com/page",
        url="https://example.com/page",
        depth=0,
        lease_token="heartbeat-token",
        lease_expires_at=9999999999999,
    )


def _make_worker(
    config: CrawlerConfig,
    renew_returns: list[bool] | bool = True,
    processing_delay: float = 0.0,
) -> Worker:
    """Create a Worker with mocked collaborators for heartbeat testing.

    Args:
        config: CrawlerConfig with lease_timeout_ms.
        renew_returns: Bool or list of bools for successive renew_lease calls.
        processing_delay: How long _do_process "takes" (simulated with sleep).
    """
    worker = Worker()
    worker.config = config

    # Track renew calls
    if isinstance(renew_returns, bool):
        worker.metadata_store = AsyncMock()
        worker.metadata_store.renew_lease = AsyncMock(return_value=renew_returns)
    else:
        worker.metadata_store = AsyncMock()
        worker.metadata_store.renew_lease = AsyncMock(side_effect=renew_returns)

    # Mark completed mock (for _do_process to succeed)
    worker.metadata_store.mark_completed = AsyncMock()
    worker.metadata_store.get_redirect_count = AsyncMock(return_value=0)
    worker.metadata_store.enqueue = AsyncMock()

    # Rate limiter — calls the passed fn (which is lambda: fetcher.fetch(url))
    async def _execute_fn(fn):
        if processing_delay > 0:
            await asyncio.sleep(processing_delay)
        return FetchResponse(
            status_code=200,
            headers={"content-type": "text/html"},
            body=b"<html>ok</html>",
        )

    worker.rate_limiter = AsyncMock()
    worker.rate_limiter.execute = AsyncMock(side_effect=_execute_fn)

    # Content dispatcher
    worker.content_dispatcher = AsyncMock()
    worker.content_dispatcher.process = AsyncMock(
        return_value=ProcessorResult(
            discovered_urls=[],
            metadata={"page_title": "T", "link_count": 0},
            content_hash="abc",
            file_path="output/html/abc.html",
        )
    )

    # URL normalizer and filter
    from unittest.mock import Mock

    worker.url_normalizer = Mock()
    worker.url_normalizer.normalize = Mock(return_value=None)  # No links to enqueue
    worker.url_filter = AsyncMock()
    worker.url_filter.passes = AsyncMock(return_value=False)
    worker.fetcher = AsyncMock()

    return worker


# ---------------------------------------------------------------------------
# Heartbeat fires at correct interval
# ---------------------------------------------------------------------------


class TestHeartbeatInterval:
    """Heartbeat fires at 50% of lease_timeout_ms."""

    @pytest.mark.asyncio
    async def test_heartbeat_renews_during_long_processing(self) -> None:
        """With lease_timeout=200ms, heartbeat fires at 100ms intervals.

        If processing takes 350ms, heartbeat should fire ~3 times.
        """
        config = _make_config(lease_timeout_ms=200)
        worker = _make_worker(config, renew_returns=True, processing_delay=0.35)

        await worker.process_url(_make_lease())

        # With 200ms timeout, interval is 100ms. In 350ms, expect 3 renewals.
        renew_count = worker.metadata_store.renew_lease.call_count
        assert renew_count == 3  # max_renewals = 3


# ---------------------------------------------------------------------------
# Heartbeat stops after max_renewals (3)
# ---------------------------------------------------------------------------


class TestHeartbeatMaxRenewals:
    """Heartbeat stops after 3 successful renewals."""

    @pytest.mark.asyncio
    async def test_heartbeat_stops_after_three_renewals(self) -> None:
        """Even with infinite processing time, heartbeat caps at 3 calls."""
        config = _make_config(lease_timeout_ms=100)  # 50ms interval
        worker = _make_worker(config, renew_returns=True, processing_delay=0.5)

        await worker.process_url(_make_lease())

        # Should have called renew_lease exactly 3 times (max_renewals)
        assert worker.metadata_store.renew_lease.call_count == 3


# ---------------------------------------------------------------------------
# Heartbeat stops early on renew failure
# ---------------------------------------------------------------------------


class TestHeartbeatStopsOnFailure:
    """Heartbeat stops immediately if renew_lease returns False."""

    @pytest.mark.asyncio
    async def test_heartbeat_stops_when_lease_stolen(self) -> None:
        """If renew_lease returns False, heartbeat breaks immediately."""
        config = _make_config(lease_timeout_ms=100)  # 50ms interval
        # First renewal succeeds, second fails
        worker = _make_worker(config, renew_returns=[True, False], processing_delay=0.3)

        await worker.process_url(_make_lease())

        # Should have called renew_lease exactly 2 times (stopped on False)
        assert worker.metadata_store.renew_lease.call_count == 2


# ---------------------------------------------------------------------------
# Heartbeat cancelled when processing finishes early
# ---------------------------------------------------------------------------


class TestHeartbeatCancelledOnCompletion:
    """Heartbeat is cleanly cancelled when process_url returns."""

    @pytest.mark.asyncio
    async def test_fast_processing_cancels_heartbeat(self) -> None:
        """If processing completes before first heartbeat, no renewals occur."""
        config = _make_config(lease_timeout_ms=10000)  # 5s interval
        worker = _make_worker(config, renew_returns=True, processing_delay=0.0)

        result = await worker.process_url(_make_lease())

        # Processing was instant, heartbeat never got to sleep(5s)
        assert worker.metadata_store.renew_lease.call_count == 0
        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_heartbeat_does_not_prevent_normal_completion(self) -> None:
        """Worker returns its result normally regardless of heartbeat state."""
        config = _make_config(lease_timeout_ms=200)
        worker = _make_worker(config, renew_returns=True, processing_delay=0.15)

        result = await worker.process_url(_make_lease())

        assert result.status == "completed"
        assert result.normalized_url == "https://example.com/page"
