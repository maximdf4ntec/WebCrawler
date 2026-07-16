"""
Unit tests for MetadataStore state transitions and queries (Task 3.3).

Tests:
- mark_completed() with lease token validation (stale write rejection)
- mark_retry() with retry count and next_retry_at
- mark_terminal_failed() and mark_failed()
- exists(), get_content_hash(), get_urls_by_state(), get_state_counts()
- get_child_urls(), get_redirect_count()
- Type-specific metadata storage (HTML, Image, Video, PDF)

These tests use real SQLite in :memory: mode.
"""

import time

import pytest
import pytest_asyncio

from crawler.metadata_store import MetadataStore
from crawler.types import (
    CrawlState,
    HtmlMetadata,
    ImageMetadata,
    VideoMetadata,
    PdfMetadata,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def store() -> MetadataStore:
    """Create an initialized in-memory MetadataStore with a test URL leased."""
    s = MetadataStore(db_path=":memory:")
    await s.init()
    yield s
    await s.close()


async def _enqueue_and_lease(store: MetadataStore, url: str, depth: int = 0) -> str:
    """Helper: enqueue a URL and acquire a lease, return the lease token."""
    await store.enqueue(url, url, depth=depth)
    leases = await store.acquire_lease_batch(batch_size=1, lease_ttl_ms=60000)
    assert len(leases) == 1
    return leases[0].lease_token


# ---------------------------------------------------------------------------
# mark_completed() — Lease-validated state transition
# ---------------------------------------------------------------------------


class TestMarkCompleted:
    """MetadataStore.mark_completed() transitions In_Progress → Completed."""

    @pytest.mark.asyncio
    async def test_mark_completed_with_valid_token(self, store: MetadataStore) -> None:
        """Completes the URL and stores content hash, content type, and metadata."""
        token = await _enqueue_and_lease(store, "https://example.com/page")

        result = await store.mark_completed(
            "https://example.com/page",
            lease_token=token,
            content_hash="abc123def456",
            content_type="text/html",
            metadata={"page_title": "Hello", "link_count": 5},
        )

        assert result is True

        cursor = await store.db.execute(
            "SELECT crawl_state, content_hash, content_type, lease_token, lease_expires_at "
            "FROM url_records WHERE normalized_url = ?",
            ("https://example.com/page",),
        )
        row = await cursor.fetchone()
        assert row["crawl_state"] == "Completed"
        assert row["content_hash"] == "abc123def456"
        assert row["content_type"] == "text/html"
        assert row["lease_token"] is None
        assert row["lease_expires_at"] is None

    @pytest.mark.asyncio
    async def test_mark_completed_rejects_wrong_token(
        self, store: MetadataStore
    ) -> None:
        """A stale/wrong lease token causes the write to be rejected."""
        await _enqueue_and_lease(store, "https://example.com/page")

        # Attempt completion with wrong token — should return False
        result = await store.mark_completed(
            "https://example.com/page",
            lease_token="wrong-token-xyz",
            content_hash="abc123",
            content_type="text/html",
        )

        assert result is False

        cursor = await store.db.execute(
            "SELECT crawl_state FROM url_records WHERE normalized_url = ?",
            ("https://example.com/page",),
        )
        row = await cursor.fetchone()
        assert row["crawl_state"] == "In_Progress"  # unchanged

    @pytest.mark.asyncio
    async def test_mark_completed_rejects_if_not_in_progress(
        self, store: MetadataStore
    ) -> None:
        """Only In_Progress URLs can be marked completed."""
        await store.enqueue(
            "https://example.com/pending", "https://example.com/pending", depth=0
        )

        # URL is in Pending state — should reject and return False
        result = await store.mark_completed(
            "https://example.com/pending",
            lease_token="any-token",
            content_hash="abc",
            content_type="text/html",
        )

        assert result is False

        cursor = await store.db.execute(
            "SELECT crawl_state FROM url_records WHERE normalized_url = ?",
            ("https://example.com/pending",),
        )
        row = await cursor.fetchone()
        assert row["crawl_state"] == "Pending"

    @pytest.mark.asyncio
    async def test_mark_completed_sets_last_crawl_timestamp(
        self, store: MetadataStore
    ) -> None:
        """Completion sets the last_crawl_timestamp field."""
        token = await _enqueue_and_lease(store, "https://example.com/ts")

        result = await store.mark_completed(
            "https://example.com/ts",
            lease_token=token,
            content_hash="hash1",
            content_type="text/html",
        )

        assert result is True

        cursor = await store.db.execute(
            "SELECT last_crawl_timestamp FROM url_records WHERE normalized_url = ?",
            ("https://example.com/ts",),
        )
        row = await cursor.fetchone()
        assert row["last_crawl_timestamp"] is not None


# ---------------------------------------------------------------------------
# mark_retry() — Retry with backoff scheduling
# ---------------------------------------------------------------------------


class TestMarkRetry:
    """MetadataStore.mark_retry() transitions In_Progress → Retry."""

    @pytest.mark.asyncio
    async def test_mark_retry_sets_state_and_fields(self, store: MetadataStore) -> None:
        """Sets state to Retry with retry_count, next_retry_at, and reason."""
        token = await _enqueue_and_lease(store, "https://example.com/fail")
        next_at = int(time.time() * 1000) + 5000

        result = await store.mark_retry(
            "https://example.com/fail",
            lease_token=token,
            retry_count=1,
            next_retry_at=next_at,
            reason="server error",
        )

        assert result is True

        cursor = await store.db.execute(
            "SELECT crawl_state, retry_count, next_retry_at, failure_reason "
            "FROM url_records WHERE normalized_url = ?",
            ("https://example.com/fail",),
        )
        row = await cursor.fetchone()
        assert row["crawl_state"] == "Retry"
        assert row["retry_count"] == 1
        assert row["next_retry_at"] == next_at
        assert row["failure_reason"] == "server error"

    @pytest.mark.asyncio
    async def test_mark_retry_rejects_wrong_token(self, store: MetadataStore) -> None:
        """Wrong lease token leaves state unchanged and returns False."""
        await _enqueue_and_lease(store, "https://example.com/x")

        result = await store.mark_retry(
            "https://example.com/x",
            lease_token="bad-token",
            retry_count=1,
            next_retry_at=int(time.time() * 1000) + 5000,
            reason="timeout",
        )

        assert result is False

        cursor = await store.db.execute(
            "SELECT crawl_state FROM url_records WHERE normalized_url = ?",
            ("https://example.com/x",),
        )
        row = await cursor.fetchone()
        assert row["crawl_state"] == "In_Progress"


# ---------------------------------------------------------------------------
# mark_terminal_failed() — Permanent failure
# ---------------------------------------------------------------------------


class TestMarkTerminalFailed:
    """MetadataStore.mark_terminal_failed() transitions In_Progress → Terminal_Failed."""

    @pytest.mark.asyncio
    async def test_mark_terminal_failed_sets_state_and_reason(
        self, store: MetadataStore
    ) -> None:
        """Sets Terminal_Failed state with failure reason, clears lease."""
        token = await _enqueue_and_lease(store, "https://example.com/gone")

        result = await store.mark_terminal_failed(
            "https://example.com/gone",
            lease_token=token,
            reason="not found",
        )

        assert result is True

        cursor = await store.db.execute(
            "SELECT crawl_state, failure_reason, lease_token, lease_expires_at "
            "FROM url_records WHERE normalized_url = ?",
            ("https://example.com/gone",),
        )
        row = await cursor.fetchone()
        assert row["crawl_state"] == "Terminal_Failed"
        assert row["failure_reason"] == "not found"
        assert row["lease_token"] is None
        assert row["lease_expires_at"] is None

    @pytest.mark.asyncio
    async def test_mark_terminal_failed_rejects_wrong_token(
        self, store: MetadataStore
    ) -> None:
        """Wrong token leaves state unchanged and returns False."""
        await _enqueue_and_lease(store, "https://example.com/x")

        result = await store.mark_terminal_failed(
            "https://example.com/x",
            lease_token="stale",
            reason="blocked",
        )

        assert result is False

        cursor = await store.db.execute(
            "SELECT crawl_state FROM url_records WHERE normalized_url = ?",
            ("https://example.com/x",),
        )
        row = await cursor.fetchone()
        assert row["crawl_state"] == "In_Progress"


# ---------------------------------------------------------------------------
# mark_failed() — Max retries exceeded
# ---------------------------------------------------------------------------


class TestMarkFailed:
    """MetadataStore.mark_failed() transitions Retry → Failed."""

    @pytest.mark.asyncio
    async def test_mark_failed_sets_state(self, store: MetadataStore) -> None:
        """Transitions URL from Retry to Failed state (max retries exhausted)."""
        token = await _enqueue_and_lease(store, "https://example.com/exhausted")

        # First put it in Retry state
        retry_result = await store.mark_retry(
            "https://example.com/exhausted",
            lease_token=token,
            retry_count=3,
            next_retry_at=int(time.time() * 1000),
            reason="repeated failure",
        )
        assert retry_result is True

        result = await store.mark_failed(
            "https://example.com/exhausted", reason="max retries exceeded"
        )
        assert result is True

        cursor = await store.db.execute(
            "SELECT crawl_state, failure_reason FROM url_records WHERE normalized_url = ?",
            ("https://example.com/exhausted",),
        )
        row = await cursor.fetchone()
        assert row["crawl_state"] == "Failed"
        assert row["failure_reason"] == "max retries exceeded"


# ---------------------------------------------------------------------------
# exists() — URL existence check
# ---------------------------------------------------------------------------


class TestExists:
    """MetadataStore.exists() checks URL presence in the store."""

    @pytest.mark.asyncio
    async def test_returns_true_for_existing_url(self, store: MetadataStore) -> None:
        await store.enqueue(
            "https://example.com/here", "https://example.com/here", depth=0
        )
        assert await store.exists("https://example.com/here") is True

    @pytest.mark.asyncio
    async def test_returns_false_for_missing_url(self, store: MetadataStore) -> None:
        assert await store.exists("https://example.com/nowhere") is False


# ---------------------------------------------------------------------------
# get_content_hash()
# ---------------------------------------------------------------------------


class TestGetContentHash:
    """MetadataStore.get_content_hash() retrieves stored hash."""

    @pytest.mark.asyncio
    async def test_returns_hash_after_completion(self, store: MetadataStore) -> None:
        token = await _enqueue_and_lease(store, "https://example.com/hashed")
        await store.mark_completed(
            "https://example.com/hashed",
            lease_token=token,
            content_hash="deadbeef1234",
            content_type="text/html",
        )

        result = await store.get_content_hash("https://example.com/hashed")
        assert result == "deadbeef1234"

    @pytest.mark.asyncio
    async def test_returns_none_for_uncompleted_url(self, store: MetadataStore) -> None:
        await store.enqueue(
            "https://example.com/pending", "https://example.com/pending", depth=0
        )
        # Prove the URL exists (store is real), but has no hash yet
        assert await store.exists("https://example.com/pending") is True
        result = await store.get_content_hash("https://example.com/pending")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_missing_url(self, store: MetadataStore) -> None:
        # Prove the URL genuinely doesn't exist in the store
        assert await store.exists("https://example.com/nonexistent") is False
        result = await store.get_content_hash("https://example.com/nonexistent")
        assert result is None


# ---------------------------------------------------------------------------
# get_urls_by_state()
# ---------------------------------------------------------------------------


class TestGetUrlsByState:
    """MetadataStore.get_urls_by_state() returns all records matching a state."""

    @pytest.mark.asyncio
    async def test_returns_matching_urls(self, store: MetadataStore) -> None:
        await store.enqueue("https://example.com/a", "https://example.com/a", depth=0)
        await store.enqueue("https://example.com/b", "https://example.com/b", depth=1)

        results = await store.get_urls_by_state("Pending")
        urls = [r["normalized_url"] for r in results]
        assert "https://example.com/a" in urls
        assert "https://example.com/b" in urls

    @pytest.mark.asyncio
    async def test_returns_empty_for_no_matches(self, store: MetadataStore) -> None:
        results = await store.get_urls_by_state("Completed")
        assert results == []


# ---------------------------------------------------------------------------
# get_state_counts()
# ---------------------------------------------------------------------------


class TestGetStateCounts:
    """MetadataStore.get_state_counts() returns count per crawl state."""

    @pytest.mark.asyncio
    async def test_returns_counts_for_all_states(self, store: MetadataStore) -> None:
        await store.enqueue("https://example.com/a", "https://example.com/a", depth=0)
        await store.enqueue("https://example.com/b", "https://example.com/b", depth=0)
        # Lease one
        await store.acquire_lease_batch(batch_size=1, lease_ttl_ms=60000)

        counts = await store.get_state_counts()
        assert counts["Pending"] == 1
        assert counts["In_Progress"] == 1
        assert counts.get("Completed", 0) == 0

    @pytest.mark.asyncio
    async def test_returns_zero_for_empty_store(self, store: MetadataStore) -> None:
        counts = await store.get_state_counts()
        assert counts.get("Pending", 0) == 0
        assert counts.get("In_Progress", 0) == 0
        assert counts.get("Completed", 0) == 0


# ---------------------------------------------------------------------------
# get_child_urls()
# ---------------------------------------------------------------------------


class TestGetChildUrls:
    """MetadataStore.get_child_urls() returns URLs discovered from a parent."""

    @pytest.mark.asyncio
    async def test_returns_children_of_parent(self, store: MetadataStore) -> None:
        # Parent
        await store.enqueue("https://example.com/", "https://example.com/", depth=0)
        # Children
        await store.enqueue(
            "https://example.com/child1",
            "https://example.com/child1",
            depth=1,
            parent_url="https://example.com/",
        )
        await store.enqueue(
            "https://example.com/child2",
            "https://example.com/child2",
            depth=1,
            parent_url="https://example.com/",
        )

        children = await store.get_child_urls("https://example.com/")
        urls = [c["normalized_url"] for c in children]
        assert len(urls) == 2
        assert "https://example.com/child1" in urls
        assert "https://example.com/child2" in urls

    @pytest.mark.asyncio
    async def test_returns_empty_for_no_children(self, store: MetadataStore) -> None:
        await store.enqueue(
            "https://example.com/leaf", "https://example.com/leaf", depth=0
        )
        children = await store.get_child_urls("https://example.com/leaf")
        assert children == []


# ---------------------------------------------------------------------------
# get_redirect_count()
# ---------------------------------------------------------------------------


class TestGetRedirectCount:
    """MetadataStore.get_redirect_count() returns the redirect chain length."""

    @pytest.mark.asyncio
    async def test_returns_redirect_count(self, store: MetadataStore) -> None:
        await store.enqueue(
            "https://example.com/redir",
            "https://example.com/redir",
            depth=0,
            redirect_count=3,
        )
        count = await store.get_redirect_count("https://example.com/redir")
        assert count == 3

    @pytest.mark.asyncio
    async def test_returns_zero_for_no_redirects(self, store: MetadataStore) -> None:
        await store.enqueue(
            "https://example.com/direct", "https://example.com/direct", depth=0
        )
        count = await store.get_redirect_count("https://example.com/direct")
        assert count == 0


# ---------------------------------------------------------------------------
# Type-specific metadata storage
# ---------------------------------------------------------------------------


class TestStoreHtmlMetadata:
    """MetadataStore.store_html_metadata() persists HTML-specific data."""

    @pytest.mark.asyncio
    async def test_stores_html_metadata(self, store: MetadataStore) -> None:
        await store.enqueue(
            "https://example.com/page", "https://example.com/page", depth=0
        )
        meta = HtmlMetadata(page_title="Test Page", link_count=42)

        await store.store_html_metadata("https://example.com/page", meta)

        cursor = await store.db.execute(
            "SELECT page_title, link_count FROM html_metadata WHERE normalized_url = ?",
            ("https://example.com/page",),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["page_title"] == "Test Page"
        assert row["link_count"] == 42


class TestStoreImageMetadata:
    """MetadataStore.store_image_metadata() persists image-specific data."""

    @pytest.mark.asyncio
    async def test_stores_image_metadata_with_dimensions(
        self, store: MetadataStore
    ) -> None:
        await store.enqueue(
            "https://example.com/img.png", "https://example.com/img.png", depth=0
        )
        meta = ImageMetadata(width=1920, height=1080, file_size_bytes=2048000)

        await store.store_image_metadata("https://example.com/img.png", meta)

        cursor = await store.db.execute(
            "SELECT width, height, file_size_bytes FROM image_metadata WHERE normalized_url = ?",
            ("https://example.com/img.png",),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["width"] == 1920
        assert row["height"] == 1080
        assert row["file_size_bytes"] == 2048000

    @pytest.mark.asyncio
    async def test_stores_image_metadata_with_null_dimensions(
        self, store: MetadataStore
    ) -> None:
        await store.enqueue(
            "https://example.com/bad.png", "https://example.com/bad.png", depth=0
        )
        meta = ImageMetadata(width=None, height=None, file_size_bytes=512)

        await store.store_image_metadata("https://example.com/bad.png", meta)

        cursor = await store.db.execute(
            "SELECT width, height, file_size_bytes FROM image_metadata WHERE normalized_url = ?",
            ("https://example.com/bad.png",),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["width"] is None
        assert row["height"] is None
        assert row["file_size_bytes"] == 512


class TestStoreVideoMetadata:
    """MetadataStore.store_video_metadata() persists video-specific data."""

    @pytest.mark.asyncio
    async def test_stores_video_metadata(self, store: MetadataStore) -> None:
        await store.enqueue(
            "https://example.com/vid.mp4", "https://example.com/vid.mp4", depth=0
        )
        meta = VideoMetadata(file_size_bytes=50000000, duration_seconds=120.5)

        await store.store_video_metadata("https://example.com/vid.mp4", meta)

        cursor = await store.db.execute(
            "SELECT file_size_bytes, duration_seconds FROM video_metadata WHERE normalized_url = ?",
            ("https://example.com/vid.mp4",),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["file_size_bytes"] == 50000000
        assert row["duration_seconds"] == pytest.approx(120.5)

    @pytest.mark.asyncio
    async def test_stores_video_metadata_without_duration(
        self, store: MetadataStore
    ) -> None:
        await store.enqueue(
            "https://example.com/v.webm", "https://example.com/v.webm", depth=0
        )
        meta = VideoMetadata(file_size_bytes=1000, duration_seconds=None)

        await store.store_video_metadata("https://example.com/v.webm", meta)

        cursor = await store.db.execute(
            "SELECT duration_seconds FROM video_metadata WHERE normalized_url = ?",
            ("https://example.com/v.webm",),
        )
        row = await cursor.fetchone()
        assert row["duration_seconds"] is None


class TestStorePdfMetadata:
    """MetadataStore.store_pdf_metadata() persists PDF-specific data."""

    @pytest.mark.asyncio
    async def test_stores_pdf_metadata(self, store: MetadataStore) -> None:
        await store.enqueue(
            "https://example.com/doc.pdf", "https://example.com/doc.pdf", depth=0
        )
        meta = PdfMetadata(page_count=42, document_title="Annual Report")

        await store.store_pdf_metadata("https://example.com/doc.pdf", meta)

        cursor = await store.db.execute(
            "SELECT page_count, document_title FROM pdf_metadata WHERE normalized_url = ?",
            ("https://example.com/doc.pdf",),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["page_count"] == 42
        assert row["document_title"] == "Annual Report"

    @pytest.mark.asyncio
    async def test_stores_pdf_metadata_with_nulls(self, store: MetadataStore) -> None:
        await store.enqueue(
            "https://example.com/bad.pdf", "https://example.com/bad.pdf", depth=0
        )
        meta = PdfMetadata(page_count=None, document_title=None)

        await store.store_pdf_metadata("https://example.com/bad.pdf", meta)

        cursor = await store.db.execute(
            "SELECT page_count, document_title FROM pdf_metadata WHERE normalized_url = ?",
            ("https://example.com/bad.pdf",),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["page_count"] is None
        assert row["document_title"] is None
