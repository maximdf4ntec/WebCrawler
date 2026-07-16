"""
Unit tests for HtmlProcessor (Task 7.3).

Tests:
- Extracts links from <a href>, <img src>, <video src>, <script src>
- Extracts page title from <title> element
- Returns empty string when <title> is absent
- Resolves relative URLs against base URL
- Ignores non-HTTP resolved URLs (mailto:, javascript:, etc.)
- Returns correct link_count in metadata
- Computes SHA-256 content hash of body
- Persists HTML to output/html/<hash>.html
- Skips re-persist when hash matches stored hash (only updates timestamp)
- Handles malformed/empty HTML gracefully (returns empty title and no links)
"""

import hashlib
from unittest.mock import AsyncMock

import pytest

from crawler.processors.html_processor import HtmlProcessor
from crawler.types import FetchResponse, LeaseResult, ProcessorResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_lease(url: str = "https://example.com/page") -> LeaseResult:
    return LeaseResult(
        normalized_url=url,
        url=url,
        depth=1,
        lease_token="token-abc",
        lease_expires_at=9999999999999,
    )


def _make_response(body: bytes, url: str = "https://example.com/page") -> FetchResponse:
    return FetchResponse(
        status_code=200,
        headers={"content-type": "text/html"},
        body=body,
    )


def _mock_store(stored_hash: str | None = None) -> AsyncMock:
    """Create a mock MetadataStore."""
    store = AsyncMock()
    store.get_content_hash = AsyncMock(return_value=stored_hash)
    store.update_timestamp = AsyncMock()
    return store


# ---------------------------------------------------------------------------
# Link extraction
# ---------------------------------------------------------------------------


class TestLinkExtraction:
    """HtmlProcessor extracts links from specific HTML elements."""

    @pytest.mark.asyncio
    async def test_extracts_href_from_a_tags(self) -> None:
        """Extracts href attributes from <a> tags."""
        html = b'<html><body><a href="https://example.com/page1">Link</a></body></html>'
        proc = HtmlProcessor()
        result = await proc.process(_make_response(html), _make_lease(), _mock_store())

        assert "https://example.com/page1" in result.discovered_urls

    @pytest.mark.asyncio
    async def test_extracts_src_from_img_tags(self) -> None:
        """Extracts src attributes from <img> tags."""
        html = b'<html><body><img src="https://example.com/img.png"></body></html>'
        proc = HtmlProcessor()
        result = await proc.process(_make_response(html), _make_lease(), _mock_store())

        assert "https://example.com/img.png" in result.discovered_urls

    @pytest.mark.asyncio
    async def test_extracts_src_from_video_tags(self) -> None:
        """Extracts src attributes from <video> tags."""
        html = b'<html><body><video src="https://example.com/vid.mp4"></video></body></html>'
        proc = HtmlProcessor()
        result = await proc.process(_make_response(html), _make_lease(), _mock_store())

        assert "https://example.com/vid.mp4" in result.discovered_urls

    @pytest.mark.asyncio
    async def test_extracts_src_from_script_tags(self) -> None:
        """Extracts src attributes from <script> tags."""
        html = b'<html><body><script src="https://example.com/app.js"></script></body></html>'
        proc = HtmlProcessor()
        result = await proc.process(_make_response(html), _make_lease(), _mock_store())

        assert "https://example.com/app.js" in result.discovered_urls

    @pytest.mark.asyncio
    async def test_extracts_multiple_links(self) -> None:
        """Extracts all links from a page with multiple elements."""
        html = (
            b"<html><body>"
            b'<a href="https://example.com/a">A</a>'
            b'<a href="https://example.com/b">B</a>'
            b'<img src="https://example.com/c.png">'
            b"</body></html>"
        )
        proc = HtmlProcessor()
        result = await proc.process(_make_response(html), _make_lease(), _mock_store())

        assert len(result.discovered_urls) == 3


# ---------------------------------------------------------------------------
# Title extraction
# ---------------------------------------------------------------------------


class TestTitleExtraction:
    """HtmlProcessor extracts page title from <title> element."""

    @pytest.mark.asyncio
    async def test_extracts_title(self) -> None:
        """Page title is the text content of <title>."""
        html = b"<html><head><title>My Page Title</title></head><body></body></html>"
        proc = HtmlProcessor()
        result = await proc.process(_make_response(html), _make_lease(), _mock_store())

        assert result.metadata["page_title"] == "My Page Title"

    @pytest.mark.asyncio
    async def test_empty_string_when_no_title(self) -> None:
        """Returns empty string when <title> is absent."""
        html = b"<html><head></head><body><p>content</p></body></html>"
        proc = HtmlProcessor()
        result = await proc.process(_make_response(html), _make_lease(), _mock_store())

        assert result.metadata["page_title"] == ""


# ---------------------------------------------------------------------------
# Relative URL resolution
# ---------------------------------------------------------------------------


class TestRelativeUrlResolution:
    """HtmlProcessor resolves relative URLs against the page's base URL."""

    @pytest.mark.asyncio
    async def test_resolves_relative_path(self) -> None:
        """Relative path resolved against base URL."""
        html = b'<html><body><a href="/child/page">Link</a></body></html>'
        proc = HtmlProcessor()
        lease = _make_lease("https://example.com/parent")
        result = await proc.process(
            _make_response(html, "https://example.com/parent"), lease, _mock_store()
        )

        assert "https://example.com/child/page" in result.discovered_urls

    @pytest.mark.asyncio
    async def test_resolves_relative_dot_path(self) -> None:
        """Dot-relative path resolved correctly."""
        html = b'<html><body><a href="./sibling">Link</a></body></html>'
        proc = HtmlProcessor()
        lease = _make_lease("https://example.com/dir/page")
        result = await proc.process(
            _make_response(html, "https://example.com/dir/page"), lease, _mock_store()
        )

        assert "https://example.com/dir/sibling" in result.discovered_urls

    @pytest.mark.asyncio
    async def test_absolute_url_unchanged(self) -> None:
        """Absolute URLs pass through unchanged."""
        html = b'<html><body><a href="https://other.com/page">Link</a></body></html>'
        proc = HtmlProcessor()
        result = await proc.process(_make_response(html), _make_lease(), _mock_store())

        assert "https://other.com/page" in result.discovered_urls

    @pytest.mark.asyncio
    async def test_ignores_non_http_resolved_urls(self) -> None:
        """mailto:, javascript:, etc. are excluded from discovered_urls."""
        html = (
            b"<html><body>"
            b'<a href="mailto:user@example.com">Email</a>'
            b'<a href="javascript:void(0)">JS</a>'
            b'<a href="https://example.com/valid">Valid</a>'
            b"</body></html>"
        )
        proc = HtmlProcessor()
        result = await proc.process(_make_response(html), _make_lease(), _mock_store())

        assert "https://example.com/valid" in result.discovered_urls
        for url in result.discovered_urls:
            assert url.startswith(("http://", "https://"))


# ---------------------------------------------------------------------------
# link_count metadata
# ---------------------------------------------------------------------------


class TestLinkCount:
    """HtmlProcessor metadata includes accurate link_count."""

    @pytest.mark.asyncio
    async def test_link_count_equals_discovered_urls_length(self) -> None:
        """link_count matches the number of discovered URLs."""
        html = (
            b"<html><body>"
            b'<a href="https://example.com/a">A</a>'
            b'<a href="https://example.com/b">B</a>'
            b'<img src="https://example.com/c.png">'
            b"</body></html>"
        )
        proc = HtmlProcessor()
        result = await proc.process(_make_response(html), _make_lease(), _mock_store())

        assert result.metadata["link_count"] == len(result.discovered_urls)

    @pytest.mark.asyncio
    async def test_link_count_zero_for_no_links(self) -> None:
        """link_count is 0 when page has no links."""
        html = b"<html><body><p>No links here</p></body></html>"
        proc = HtmlProcessor()
        result = await proc.process(_make_response(html), _make_lease(), _mock_store())

        assert result.metadata["link_count"] == 0


# ---------------------------------------------------------------------------
# Content hash
# ---------------------------------------------------------------------------


class TestContentHash:
    """HtmlProcessor computes SHA-256 hash of response body."""

    @pytest.mark.asyncio
    async def test_content_hash_is_sha256_of_body(self) -> None:
        """content_hash matches SHA-256 of the raw body bytes."""
        body = b"<html><body>test</body></html>"
        expected_hash = hashlib.sha256(body).hexdigest()
        proc = HtmlProcessor()
        result = await proc.process(_make_response(body), _make_lease(), _mock_store())

        assert result.content_hash == expected_hash


# ---------------------------------------------------------------------------
# File persistence
# ---------------------------------------------------------------------------


class TestFilePersistence:
    """HtmlProcessor persists to output/html/<hash>.html."""

    @pytest.mark.asyncio
    async def test_file_path_uses_hash_and_html_extension(self) -> None:
        """file_path is output/html/<content_hash>.html."""
        body = b"<html><body>file test</body></html>"
        expected_hash = hashlib.sha256(body).hexdigest()
        proc = HtmlProcessor()
        result = await proc.process(_make_response(body), _make_lease(), _mock_store())

        assert result.file_path == f"output/html/{expected_hash}.html"


# ---------------------------------------------------------------------------
# Skip re-persist on unchanged hash
# ---------------------------------------------------------------------------


class TestSkipRepersist:
    """HtmlProcessor skips re-persist when content hash is unchanged."""

    @pytest.mark.asyncio
    async def test_skips_processing_when_hash_matches_stored(self) -> None:
        """When stored hash == new hash, returns empty result and updates timestamp."""
        body = b"<html><body>cached</body></html>"
        stored_hash = hashlib.sha256(body).hexdigest()
        store = _mock_store(stored_hash=stored_hash)

        proc = HtmlProcessor()
        result = await proc.process(_make_response(body), _make_lease(), store)

        assert result.discovered_urls == []
        assert result.metadata["link_count"] == 0
        assert result.content_hash == stored_hash
        store.update_timestamp.assert_called_once()


# ---------------------------------------------------------------------------
# Malformed HTML
# ---------------------------------------------------------------------------


class TestMalformedHtml:
    """HtmlProcessor handles malformed or empty HTML gracefully."""

    @pytest.mark.asyncio
    async def test_empty_body_returns_empty_results(self) -> None:
        """Empty body → empty title, no links."""
        proc = HtmlProcessor()
        result = await proc.process(_make_response(b""), _make_lease(), _mock_store())

        assert result.metadata["page_title"] == ""
        assert result.metadata["link_count"] == 0
        assert result.discovered_urls == []

    @pytest.mark.asyncio
    async def test_garbage_html_returns_empty_results(self) -> None:
        """Non-HTML garbage → graceful degradation (no crash)."""
        proc = HtmlProcessor()
        result = await proc.process(
            _make_response(b"\x00\x01\x02\x03 not html at all"),
            _make_lease(),
            _mock_store(),
        )

        assert result.metadata["page_title"] == ""
