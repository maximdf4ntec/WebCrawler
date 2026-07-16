"""Scheduler — coordinates the crawl loop: frontier queries, leases, dispatch, completions.

The Scheduler is the orchestration layer that drives the entire crawl:
1. Initializes the crawl (validates config, seeds the frontier)
2. Runs the main loop (acquire leases → dispatch workers → handle results)
3. Manages retry scheduling with exponential backoff
4. Detects and recovers expired leases
5. Supports graceful shutdown

Requirements: 1.3, 2.3, 4.1, 4.4, 8.1, 8.2, 8.4, 8.5, 8.6, 17.1, 17.2, 17.3, 17.4
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Optional
from urllib.parse import urlparse

import httpx

from crawler.logger import get_logger
from crawler.types import CrawlerConfig, LeaseResult, WorkerResult
from crawler.url_normalizer import URLNormalizer
from crawler.worker_pool import WorkerPool

if TYPE_CHECKING:
    from crawler.content_dispatcher import ContentDispatcher
    from crawler.metadata_store import MetadataStore
    from crawler.rate_limiter import RateLimiter
    from crawler.url_filter import URLFilter

logger = get_logger()


class Scheduler:
    """Coordinates the crawl loop: queries frontier, dispatches workers, handles outcomes.

    Usage:
        scheduler = Scheduler()
        await scheduler.init(config, store)
        await scheduler.run()       # blocks until crawl completes or shutdown
        await scheduler.shutdown()  # graceful stop
    """

    def __init__(self) -> None:
        self._config: Optional[CrawlerConfig] = None
        self._metadata_store: Optional[MetadataStore] = None
        self._worker_pool: Optional[WorkerPool] = None
        self._shutdown_requested = False
        self._poll_interval_s = 0.5
        self._url_normalizer = URLNormalizer()

        # Collaborators injected by the Crawler bootstrap before init/run
        self.rate_limiter: Optional[RateLimiter] = None
        self.content_dispatcher: Optional[ContentDispatcher] = None
        self.url_filter: Optional[URLFilter] = None
        self.http_client: Optional[httpx.AsyncClient] = None

    async def init(self, config: CrawlerConfig, store: "MetadataStore") -> None:
        """Initialize the scheduler: validate config, seed the frontier.

        Args:
            config: Crawl configuration.
            store: Initialized MetadataStore instance.

        Raises:
            ValueError: If the seed URL cannot be normalized.
        """
        self._config = config
        self._metadata_store = store

        # Normalize and validate the seed URL (Req 1.3)
        normalized_seed = self._url_normalizer.normalize(config.seed_url)
        if not normalized_seed:
            raise ValueError(f"Invalid seed URL: {config.seed_url}")

        # Extract seed domain for config storage
        parsed = urlparse(normalized_seed)
        seed_domain = parsed.netloc

        # Store config in metadata store
        await store.store_config(config, seed_domain)

        # Enqueue the seed URL at depth 0 (Req 1.3)
        await store.enqueue(normalized_seed, config.seed_url, depth=0)

        # Create worker pool with the worker callback
        self._worker_pool = WorkerPool(
            max_concurrency=config.max_concurrency,
            worker_fn=self._process_lease,
        )

        logger.info(
            "scheduler_initialized",
            seed_url=config.seed_url,
            normalized_seed=normalized_seed,
            max_concurrency=config.max_concurrency,
        )

    async def run(self) -> None:
        """Run the main crawl loop until frontier is exhausted or shutdown requested.

        Algorithm:
        1. Check available worker slots
        2. If no slots, wait for one to free up
        3. Acquire a batch of leases (capped by available slots)
        4. Dispatch each lease to the worker pool
        5. Periodically expire stale leases and log progress
        6. Terminate when no processable URLs remain
        """
        last_progress_time = time.time()
        last_expire_time = 0.0  # Ensure first iteration always checks

        while not self._shutdown_requested:
            now = time.time()

            # Periodic housekeeping
            last_expire_time = await self._maybe_expire_leases(now, last_expire_time)
            last_progress_time = await self._maybe_log_progress(now, last_progress_time)

            # Check available worker capacity
            available_slots = (
                self._config.max_concurrency - self._worker_pool.active_count()
            )
            if available_slots <= 0:
                await self._worker_pool.wait_for_slot()
                continue

            # Acquire lease batch capped by available slots
            lease_count = min(available_slots, self._config.batch_size)
            batch = await self._metadata_store.acquire_lease_batch(
                lease_count, self._config.lease_timeout_ms
            )

            if not batch:
                if await self._is_crawl_complete():
                    break
                await asyncio.sleep(self._poll_interval_s)
                continue

            # Dispatch batch to worker pool
            for lease in batch:
                if self._shutdown_requested:
                    break
                await self._worker_pool.dispatch(lease)

            await asyncio.sleep(self._poll_interval_s)

        # Log final stats on exit (Req 17.4)
        await self._log_final_stats()

    # ------------------------------------------------------------------
    # Run-loop helpers
    # ------------------------------------------------------------------

    async def _maybe_expire_leases(self, now: float, last_expire_time: float) -> float:
        """Expire stale leases if the poll interval has elapsed (Req 4.4).

        Returns:
            Updated last_expire_time.
        """
        if now - last_expire_time >= self._poll_interval_s:
            expired = await self._metadata_store.expire_leases()
            if expired > 0:
                logger.info("leases_expired", count=expired)
            return now
        return last_expire_time

    async def _maybe_log_progress(self, now: float, last_progress_time: float) -> float:
        """Emit a progress log entry if the configured interval has elapsed.

        Returns:
            Updated last_progress_time.
        """
        if now - last_progress_time >= (self._config.progress_interval_ms / 1000):
            await self._log_progress()
            return now
        return last_progress_time

    async def _is_crawl_complete(self) -> bool:
        """Check terminal condition: no processable URLs remain (Req 17.1)."""
        counts = await self._metadata_store.get_state_counts()
        pending = counts.get("Pending", 0)
        retry = counts.get("Retry", 0)
        in_progress = counts.get("In_Progress", 0)
        return pending == 0 and retry == 0 and in_progress == 0

    async def shutdown(self) -> None:
        """Graceful shutdown: stop dispatching, drain the worker pool (Req 17.2, 17.3).

        Sets the shutdown flag to stop the run loop, then waits for all
        active workers to complete before returning.
        """
        self._shutdown_requested = True
        logger.info("shutdown_requested")

        if self._worker_pool:
            await self._worker_pool.drain()

        # Log final crawl statistics (Req 17.4)
        await self._log_final_stats()
        logger.info("shutdown_complete")

    # ------------------------------------------------------------------
    # Worker callback
    # ------------------------------------------------------------------

    async def _process_lease(self, lease: LeaseResult) -> None:
        """Process a single lease: invoke the worker and handle the result.

        This is the callback passed to WorkerPool. It wraps the actual
        Worker.process_url call and handles the outcome (retry, failure, etc.).
        """
        # Import here to avoid circular imports at module level
        from crawler.worker import Worker

        worker = self._create_worker()
        try:
            result = await worker.process_url(lease)
            await self._handle_worker_result(lease, result)
        except Exception as e:
            # Unexpected worker crash — treat as transient error
            logger.error(
                url=lease.normalized_url,
                error_type="transient",
                error_message=f"worker crashed: {e}",
                component="scheduler",
            )
            await self._handle_retry(lease, str(e))

    def _create_worker(self) -> "Worker":
        """Create a Worker instance with all collaborators attached.

        Wires config, metadata_store, url_normalizer, and any additional
        collaborators (rate_limiter, content_dispatcher, url_filter, http_client)
        that were injected into the scheduler by the application bootstrap.
        """
        from crawler.worker import Worker

        worker = Worker()
        worker.config = self._config
        worker.metadata_store = self._metadata_store
        worker.url_normalizer = self._url_normalizer

        # Wire optional collaborators injected by the Crawler bootstrap
        if self.rate_limiter is not None:
            worker.rate_limiter = self.rate_limiter
        if self.content_dispatcher is not None:
            worker.content_dispatcher = self.content_dispatcher
        if self.url_filter is not None:
            worker.url_filter = self.url_filter
        if self.http_client is not None:
            worker.http_client = self.http_client

        return worker

    # ------------------------------------------------------------------
    # Result handling
    # ------------------------------------------------------------------

    async def _handle_worker_result(
        self, lease: LeaseResult, result: WorkerResult
    ) -> None:
        """Route worker results to the appropriate handler.

        Args:
            lease: The original lease that was processed.
            result: The WorkerResult from the worker.
        """
        match result.status:
            case "completed":
                # Already marked completed by the worker itself
                logger.info(
                    "url_completed",
                    url=lease.normalized_url,
                    content_type=result.content_type,
                )
            case "retry":
                await self._handle_retry(
                    lease, result.failure_reason or "unknown error"
                )
            case "terminal_failed":
                # Mark terminal failure in store (Req 8.6)
                await self._metadata_store.mark_terminal_failed(
                    lease.normalized_url,
                    lease.lease_token,
                    failure_reason=result.failure_reason or "permanent error",
                )
                logger.error(
                    url=lease.normalized_url,
                    error_type="permanent",
                    error_message=result.failure_reason or "permanent error",
                    component="worker",
                )

    async def _handle_retry(self, lease: LeaseResult, reason: str) -> None:
        """Handle a retry outcome: compute backoff or mark as failed.

        If the retry count has reached max_retries, marks the URL as Failed.
        Otherwise, schedules a retry with exponential backoff (Req 8.1, 8.2, 8.4).

        Args:
            lease: The lease that needs retrying.
            reason: Human-readable failure reason.
        """
        # Get current retry count from the store
        retry_count = await self._get_retry_count(lease.normalized_url)
        next_attempt = retry_count + 1

        if next_attempt > self._config.max_retries:
            # Max retries exceeded → mark as Failed (Req 8.4, 8.5)
            await self._metadata_store.mark_failed(
                lease.normalized_url,
                lease_token=lease.lease_token,
                failure_reason=f"max retries ({self._config.max_retries}) exceeded: {reason}",
            )
            logger.error(
                url=lease.normalized_url,
                error_type="transient",
                error_message=f"max retries exceeded: {reason}",
                retry_count=retry_count,
                component="scheduler",
            )
        else:
            # Schedule retry with exponential backoff (Req 8.1, 8.2)
            delay_s = self.compute_retry_delay(next_attempt)
            next_retry_at_ms = int(time.time() * 1000) + int(delay_s * 1000)

            await self._metadata_store.mark_retry(
                lease.normalized_url,
                lease.lease_token,
                retry_count=next_attempt,
                next_retry_at=next_retry_at_ms,
                reason=reason,
            )
            logger.info(
                "retry_scheduled",
                url=lease.normalized_url,
                attempt=next_attempt,
                delay_s=delay_s,
                reason=reason,
            )

    async def _get_retry_count(self, normalized_url: str) -> int:
        """Get the current retry count for a URL from the store."""
        return await self._metadata_store.get_retry_count(normalized_url)

    # ------------------------------------------------------------------
    # Backoff computation
    # ------------------------------------------------------------------

    @staticmethod
    def compute_retry_delay(attempt: int) -> float:
        """Compute exponential backoff delay for a retry attempt.

        Formula: min(1.0 * 2^(attempt - 1), 300.0) seconds (Req 8.2)

        Args:
            attempt: The retry attempt number (1-based).

        Returns:
            Delay in seconds before the next retry.
        """
        return min(1.0 * (2 ** (attempt - 1)), 300.0)

    # ------------------------------------------------------------------
    # Progress & stats
    # ------------------------------------------------------------------

    async def _log_progress(self) -> None:
        """Log current crawl progress statistics."""
        if not self._metadata_store:
            return

        counts = await self._metadata_store.get_state_counts()
        total = sum(counts.values())
        stats = {
            "total_discovered": total,
            "completed": counts.get("Completed", 0),
            "failed": counts.get("Failed", 0) + counts.get("Terminal_Failed", 0),
            "in_progress": counts.get("In_Progress", 0),
            "queue_depth": counts.get("Pending", 0) + counts.get("Retry", 0),
        }
        logger.progress(stats)

    async def _log_final_stats(self) -> None:
        """Log final crawl statistics (Req 17.4)."""
        if not self._metadata_store:
            return

        counts = await self._metadata_store.get_state_counts()
        total = sum(counts.values())
        stats = {
            "total_discovered": total,
            "completed": counts.get("Completed", 0),
            "failed": counts.get("Failed", 0),
            "terminal_failed": counts.get("Terminal_Failed", 0),
            "pending": counts.get("Pending", 0),
            "in_progress": counts.get("In_Progress", 0),
            "retry": counts.get("Retry", 0),
        }
        logger.force_progress(stats)
