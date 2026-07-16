"""Worker — processes a single URL: fetch → process → persist → report.

Implements the core crawl loop for a single URL lease:
1. Spawns a lease heartbeat background task
2. Fetches the URL through the rate limiter
3. Dispatches based on HTTP status code (match/case)
4. Handles content processing, redirects, and error classification
5. Reports outcome as a WorkerResult

Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 5.9, 19.2
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
from typing import TYPE_CHECKING, Optional

from crawler.types import (
    CrawlerConfig,
    FetchResponse,
    LeaseResult,
    ProcessorResult,
    RateLimitExhaustedError,
    TransientError,
    WorkerResult,
)

if TYPE_CHECKING:
    from crawler.fetcher import Fetcher
    from crawler.metadata_store import MetadataStore
    from crawler.rate_limiter import RateLimiter
    from crawler.url_filter import URLFilter
    from crawler.url_normalizer import URLNormalizer


class Worker:
    """Processes a single URL: fetch → process → persist → report.

    Collaborators are injected as attributes after construction:
        config: CrawlerConfig
        rate_limiter: RateLimiter (async execute(fn) method)
        content_dispatcher: ContentDispatcher (async process(response, lease))
        metadata_store: MetadataStore
        url_normalizer: URLNormalizer (normalize(url) → Optional[str])
        url_filter: URLFilter (passes(url, depth) → bool)
        fetcher: Fetcher (pluggable fetch strategy — mock API or real HTTP)
    """

    config: CrawlerConfig
    rate_limiter: RateLimiter
    content_dispatcher: object  # ContentDispatcher (module not yet created — Task 7.1)
    metadata_store: MetadataStore
    url_normalizer: URLNormalizer
    url_filter: URLFilter
    fetcher: Fetcher

    async def process_url(self, lease: LeaseResult) -> WorkerResult:
        """Process a leased URL and return the outcome.

        Spawns a heartbeat task to renew the lease periodically, then
        delegates to _do_process for the actual fetch/dispatch logic.
        """
        heartbeat_task = asyncio.create_task(self._lease_heartbeat(lease))
        try:
            return await self._do_process(lease)
        finally:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task

    async def _do_process(self, lease: LeaseResult) -> WorkerResult:
        """Core processing logic: fetch, classify status, dispatch."""
        try:
            response = await self.rate_limiter.execute(
                lambda: self.fetcher.fetch(lease.url)
            )

            match response.status_code:
                case 200:
                    return await self._handle_200(response, lease)
                case 301 | 302:
                    return await self._handle_redirect(response, lease)
                case 404:
                    return self._terminal_failed(lease, "not found")
                case 403:
                    return self._terminal_failed(lease, "blocked")
                case 429:
                    # 429 is normally handled by rate_limiter internally.
                    # If it leaks through, treat as transient.
                    return self._transient_error(lease, "rate limited")
                case 500:
                    return self._transient_error(lease, "server error")
                case _:
                    return self._terminal_failed(
                        lease, f"unexpected status code: {response.status_code}"
                    )

        except (
            ConnectionError,
            TimeoutError,
            OSError,
            RateLimitExhaustedError,
            TransientError,
        ) as e:
            return self._transient_error(lease, str(e))
        except Exception as e:
            return self._terminal_failed(lease, f"processing error: {e}")

    # ------------------------------------------------------------------
    # Status 200 handling
    # ------------------------------------------------------------------

    async def _handle_200(
        self, response: FetchResponse, lease: LeaseResult
    ) -> WorkerResult:
        """Handle a successful 200 response: validate, hash, dispatch, enqueue.

        Performs size check in two phases per Requirement 19.2:
        1. Pre-download: If Content-Length header is present and exceeds limit,
           reject immediately without processing the body.
        2. Post-download: If Content-Length was absent, check actual body length.
        """
        if response.body is None:
            return self._terminal_failed(lease, "empty body")

        # Pre-download size check via Content-Length header (Req 19.2)
        content_length_str = response.headers.get("content-length")
        if content_length_str is not None:
            try:
                content_length = int(content_length_str)
                if content_length > self.config.max_content_size:
                    return self._terminal_failed(lease, "content too large")
            except (ValueError, TypeError):
                pass  # Invalid header value — fall through to body-length check

        # Post-download size check (when Content-Length absent or unparseable)
        if len(response.body) > self.config.max_content_size:
            return self._terminal_failed(lease, "content too large")

        content_hash = hashlib.sha256(response.body).hexdigest()

        # Dispatch to content processor
        result = await self.content_dispatcher.process(
            response, lease, self.metadata_store
        )

        # Enqueue discovered URLs
        if result and result.discovered_urls:
            await self._enqueue_discovered_urls(result.discovered_urls, lease.depth + 1)

        # Mark completed in metadata store
        content_type = response.headers.get("content-type")
        etag = response.headers.get("etag")
        metadata = result.metadata if result else None

        await self.metadata_store.mark_completed(
            lease.normalized_url,
            lease.lease_token,
            content_hash,
            content_type,
            etag,
            metadata,
        )

        return WorkerResult(
            normalized_url=lease.normalized_url,
            status="completed",
            content_hash=content_hash,
            content_type=content_type,
            metadata=metadata,
            discovered_urls=result.discovered_urls if result else [],
        )

    # ------------------------------------------------------------------
    # Status 301/302 handling
    # ------------------------------------------------------------------

    async def _handle_redirect(
        self, response: FetchResponse, lease: LeaseResult
    ) -> WorkerResult:
        """Handle a redirect: validate Location, check loop, enqueue target."""
        redirect_url = response.headers.get("location")
        if not redirect_url:
            return self._terminal_failed(lease, "missing redirect location")

        redirect_count = await self.metadata_store.get_redirect_count(
            lease.normalized_url
        )
        if redirect_count >= self.config.max_redirects:
            return self._terminal_failed(lease, "redirect loop detected")

        # Normalize and filter the redirect target
        normalized = self.url_normalizer.normalize(redirect_url)
        if normalized and await self.url_filter.passes(normalized, lease.depth):
            await self.metadata_store.enqueue(
                normalized,
                redirect_url,
                lease.depth,
                lease.url,
                redirect_count=redirect_count + 1,
            )

        # Mark the original URL as completed (redirect followed)
        await self.metadata_store.mark_completed(
            lease.normalized_url, lease.lease_token
        )

        return WorkerResult(
            normalized_url=lease.normalized_url,
            status="completed",
        )

    # ------------------------------------------------------------------
    # URL discovery
    # ------------------------------------------------------------------

    async def _enqueue_discovered_urls(self, urls: list[str], depth: int) -> None:
        """Normalize, filter, and enqueue discovered URLs."""
        for url in urls:
            normalized = self.url_normalizer.normalize(url)
            if normalized and await self.url_filter.passes(normalized, depth):
                await self.metadata_store.enqueue(normalized, url, depth, None)

    # ------------------------------------------------------------------
    # Lease heartbeat
    # ------------------------------------------------------------------

    async def _lease_heartbeat(self, lease: LeaseResult) -> None:
        """Periodically renew the lease until cancelled or max renewals reached."""
        renewal_interval = (
            self.config.lease_timeout_ms / 2 / 1000
        )  # renew at 50% of TTL
        renewals = 0
        max_renewals = 3
        while renewals < max_renewals:
            await asyncio.sleep(renewal_interval)
            success = await self.metadata_store.renew_lease(
                lease.normalized_url, lease.lease_token, self.config.lease_timeout_ms
            )
            if not success:
                break
            renewals += 1

    # ------------------------------------------------------------------
    # Result helpers
    # ------------------------------------------------------------------

    def _terminal_failed(self, lease: LeaseResult, reason: str) -> WorkerResult:
        """Build a 'terminal_failed' WorkerResult."""
        return WorkerResult(
            normalized_url=lease.normalized_url,
            status="terminal_failed",
            failure_reason=reason,
        )

    def _transient_error(self, lease: LeaseResult, reason: str) -> WorkerResult:
        """Build a 'retry' WorkerResult for transient errors."""
        return WorkerResult(
            normalized_url=lease.normalized_url,
            status="retry",
            failure_reason=reason,
        )
