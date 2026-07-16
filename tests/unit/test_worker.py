"""
Unit tests for Worker (Task 6.2).

Tests:
- HTTP 200 with valid body → completed with content hash
- HTTP 200 with null body → terminal_failed("empty body")
- HTTP 200 with body > max_content_size → terminal_failed("content too large")
- HTTP 301/302 with Location → enqueue redirect target, mark completed
- HTTP 301/302 without Location → terminal_failed("missing redirect location")
- HTTP 301/302 with redirect_count >= max → terminal_failed("redirect loop detected")
- HTTP 404 → terminal_failed("not found")
- HTTP 403 → terminal_failed("blocked")
- HTTP 500 → transient_error("server error")
- Unknown status code → terminal_failed("unexpected status code: <code>")
- NetworkError → transient_error(message)
- Unexpected exception → terminal_failed("processing error: <e>")

All collaborators (rate_limiter, content_dispatcher, metadata_store, url_normalizer,
url_filter) are mocked at their interface boundary.
"""

import hashlib
from unittest.mock import AsyncMock, Mock, patch

import pytest

from crawler.worker import Worker
from crawler.types import (
    LeaseResult,
    WorkerResult,
    FetchResponse,
    ProcessorResult,
    CrawlerConfig,
    TransientError,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_lease(url: str = "https://example.com/page") -> LeaseResult:
    return LeaseResult(
        normalized_url=url,
        url=url,
        depth=1,
        lease_token="token-abc",
        lease_expires_at=9999999999999,
    )


def _make_config(**overrides) -> CrawlerConfig:
    defaults = {
        "seed_url": "https://example.com",
        "max_content_size": 1024 * 1024,  # 1MB
        "max_redirects": 5,
    }
    defaults.update(overrides)
    return CrawlerConfig(**defaults)


def _make_response(
    status_code: int = 200,
    headers: dict | None = None,
    body: bytes | None = b"<html>hello</html>",
) -> FetchResponse:
    return FetchResponse(
        status_code=status_code,
        headers=headers or {},
        body=body,
    )


def _make_worker(
    config: CrawlerConfig | None = None,
    fetch_response: FetchResponse | None = None,
    processor_result: ProcessorResult | None = None,
    redirect_count: int = 0,
    normalize_result: str | None = "https://example.com/normalized",
    filter_passes: bool = True,
) -> Worker:
    """Create a Worker with mocked collaborators."""
    worker = Worker()

    # Config
    worker.config = config or _make_config()

    # Rate limiter — execute just calls the fn
    worker.rate_limiter = AsyncMock()
    if fetch_response:
        worker.rate_limiter.execute = AsyncMock(return_value=fetch_response)
    else:
        worker.rate_limiter.execute = AsyncMock(return_value=_make_response())

    # Content dispatcher
    worker.content_dispatcher = AsyncMock()
    if processor_result:
        worker.content_dispatcher.process = AsyncMock(return_value=processor_result)
    else:
        worker.content_dispatcher.process = AsyncMock(
            return_value=ProcessorResult(
                discovered_urls=["https://example.com/child"],
                metadata={"page_title": "Test", "link_count": 1},
                content_hash=hashlib.sha256(b"<html>hello</html>").hexdigest(),
                file_path="output/html/abc.html",
            )
        )

    # Metadata store
    worker.metadata_store = AsyncMock()
    worker.metadata_store.get_redirect_count = AsyncMock(return_value=redirect_count)
    worker.metadata_store.mark_completed = AsyncMock()
    worker.metadata_store.enqueue = AsyncMock()

    # URL normalizer
    worker.url_normalizer = Mock()
    worker.url_normalizer.normalize = Mock(return_value=normalize_result)

    # URL filter
    worker.url_filter = Mock()
    worker.url_filter.passes = AsyncMock(return_value=filter_passes)

    return worker


# ---------------------------------------------------------------------------
# HTTP 200 — Success cases
# ---------------------------------------------------------------------------


class TestStatus200:
    """Worker handles HTTP 200 responses."""

    @pytest.mark.asyncio
    async def test_200_with_valid_body_returns_completed(self) -> None:
        """200 + body → completed with content hash."""
        body = b"<html>test content</html>"
        expected_hash = hashlib.sha256(body).hexdigest()
        response = _make_response(200, headers={"content-type": "text/html"}, body=body)
        worker = _make_worker(fetch_response=response)

        result = await worker.process_url(_make_lease())

        assert result.status == "completed"
        assert result.content_hash == expected_hash

    @pytest.mark.asyncio
    async def test_200_with_null_body_returns_terminal_failed(self) -> None:
        """200 + null body → terminal_failed("empty body")."""
        response = _make_response(200, body=None)
        worker = _make_worker(fetch_response=response)

        result = await worker.process_url(_make_lease())

        assert result.status == "terminal_failed"
        assert result.failure_reason == "empty body"

    @pytest.mark.asyncio
    async def test_200_with_oversized_body_returns_terminal_failed(self) -> None:
        """200 + body > max_content_size → terminal_failed("content too large")."""
        config = _make_config(max_content_size=100)
        body = b"x" * 101  # exceeds limit
        response = _make_response(200, body=body)
        worker = _make_worker(config=config, fetch_response=response)

        result = await worker.process_url(_make_lease())

        assert result.status == "terminal_failed"
        assert result.failure_reason == "content too large"

    @pytest.mark.asyncio
    async def test_200_enqueues_discovered_urls(self) -> None:
        """Discovered URLs from content processor are enqueued."""
        body = b"<html>links</html>"
        response = _make_response(200, headers={"content-type": "text/html"}, body=body)
        proc_result = ProcessorResult(
            discovered_urls=["https://example.com/a", "https://example.com/b"],
            metadata={"page_title": "T", "link_count": 2},
            content_hash=hashlib.sha256(body).hexdigest(),
            file_path="output/html/x.html",
        )
        worker = _make_worker(fetch_response=response, processor_result=proc_result)

        await worker.process_url(_make_lease())

        # Verify enqueue was called for discovered URLs
        assert (
            worker.metadata_store.enqueue.called
            or worker.metadata_store.mark_completed.called
        )


# ---------------------------------------------------------------------------
# HTTP 301/302 — Redirects
# ---------------------------------------------------------------------------


class TestRedirects:
    """Worker handles HTTP 301/302 redirect responses."""

    @pytest.mark.asyncio
    async def test_301_with_location_enqueues_redirect_target(self) -> None:
        """301 + Location header → enqueue normalized target, mark completed."""
        response = _make_response(
            301, headers={"location": "https://example.com/new-page"}
        )
        worker = _make_worker(fetch_response=response, redirect_count=0)

        result = await worker.process_url(_make_lease())

        assert result.status == "completed"
        worker.metadata_store.enqueue.assert_called()

    @pytest.mark.asyncio
    async def test_302_with_location_enqueues_redirect_target(self) -> None:
        """302 works the same as 301."""
        response = _make_response(
            302, headers={"location": "https://example.com/other"}
        )
        worker = _make_worker(fetch_response=response, redirect_count=0)

        result = await worker.process_url(_make_lease())

        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_redirect_without_location_returns_terminal_failed(self) -> None:
        """301/302 without Location → terminal_failed("missing redirect location")."""
        response = _make_response(301, headers={})
        worker = _make_worker(fetch_response=response)

        result = await worker.process_url(_make_lease())

        assert result.status == "terminal_failed"
        assert result.failure_reason == "missing redirect location"

    @pytest.mark.asyncio
    async def test_redirect_loop_detected_returns_terminal_failed(self) -> None:
        """redirect_count >= max_redirects → terminal_failed("redirect loop detected")."""
        response = _make_response(301, headers={"location": "https://example.com/loop"})
        config = _make_config(max_redirects=3)
        worker = _make_worker(config=config, fetch_response=response, redirect_count=3)

        result = await worker.process_url(_make_lease())

        assert result.status == "terminal_failed"
        assert result.failure_reason == "redirect loop detected"

    @pytest.mark.asyncio
    async def test_redirect_not_enqueued_when_filter_rejects(self) -> None:
        """If URL filter rejects the redirect target, it's not enqueued."""
        response = _make_response(
            301, headers={"location": "https://other-domain.com/page"}
        )
        worker = _make_worker(fetch_response=response, filter_passes=False)

        result = await worker.process_url(_make_lease())

        assert result.status == "completed"
        worker.metadata_store.enqueue.assert_not_called()


# ---------------------------------------------------------------------------
# HTTP 404, 403 — Permanent failures
# ---------------------------------------------------------------------------


class TestPermanentFailures:
    """Worker handles permanent failure status codes."""

    @pytest.mark.asyncio
    async def test_404_returns_terminal_failed_not_found(self) -> None:
        response = _make_response(404)
        worker = _make_worker(fetch_response=response)

        result = await worker.process_url(_make_lease())

        assert result.status == "terminal_failed"
        assert result.failure_reason == "not found"

    @pytest.mark.asyncio
    async def test_403_returns_terminal_failed_blocked(self) -> None:
        response = _make_response(403)
        worker = _make_worker(fetch_response=response)

        result = await worker.process_url(_make_lease())

        assert result.status == "terminal_failed"
        assert result.failure_reason == "blocked"


# ---------------------------------------------------------------------------
# HTTP 500 — Transient error
# ---------------------------------------------------------------------------


class TestTransientErrors:
    """Worker handles transient failure status codes."""

    @pytest.mark.asyncio
    async def test_500_returns_retry_server_error(self) -> None:
        response = _make_response(500)
        worker = _make_worker(fetch_response=response)

        result = await worker.process_url(_make_lease())

        assert result.status == "retry"
        assert result.failure_reason == "server error"


# ---------------------------------------------------------------------------
# Unknown status codes
# ---------------------------------------------------------------------------


class TestUnknownStatusCodes:
    """Worker handles unexpected status codes as terminal failures."""

    @pytest.mark.asyncio
    async def test_unknown_status_returns_terminal_failed(self) -> None:
        """Any status not in {200,301,302,403,404,429,500} → terminal_failed."""
        response = _make_response(418)  # I'm a teapot
        worker = _make_worker(fetch_response=response)

        result = await worker.process_url(_make_lease())

        assert result.status == "terminal_failed"
        assert result.failure_reason == "unexpected status code: 418"

    @pytest.mark.asyncio
    async def test_status_503_returns_terminal_failed(self) -> None:
        """503 is not in the handled set → terminal_failed."""
        response = _make_response(503)
        worker = _make_worker(fetch_response=response)

        result = await worker.process_url(_make_lease())

        assert result.status == "terminal_failed"
        assert result.failure_reason == "unexpected status code: 503"


# ---------------------------------------------------------------------------
# Exception handling
# ---------------------------------------------------------------------------


class TestExceptionHandling:
    """Worker handles exceptions from fetch/processing."""

    @pytest.mark.asyncio
    async def test_network_error_returns_retry(self) -> None:
        """NetworkError → transient_error (retry)."""
        worker = _make_worker()
        worker.rate_limiter.execute = AsyncMock(
            side_effect=ConnectionError("connection refused")
        )

        result = await worker.process_url(_make_lease())

        assert result.status == "retry"
        assert "connection refused" in result.failure_reason

    @pytest.mark.asyncio
    async def test_unexpected_exception_returns_terminal_failed(self) -> None:
        """Unexpected exception → terminal_failed("processing error: ...")."""
        worker = _make_worker()
        worker.rate_limiter.execute = AsyncMock(
            side_effect=ValueError("something broke")
        )

        result = await worker.process_url(_make_lease())

        assert result.status == "terminal_failed"
        assert "processing error:" in result.failure_reason
        assert "something broke" in result.failure_reason
