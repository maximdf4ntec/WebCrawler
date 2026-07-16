"""
Unit tests for ImageProcessor (Task 7.5).

Tests:
- Extracts dimensions from valid image bytes
- Returns None dimensions for corrupt/undecodable image
- Records file_size_bytes from Content-Length header
- Falls back to len(body) when Content-Length absent
- Computes SHA-256 content hash
- Determines file extension from Content-Type header
- Falls back to "bin" extension for unknown MIME
- Persists to output/images/<hash>.<ext>
- discovered_urls is always empty (images don't contain links)
"""

import hashlib
import io

import pytest

from crawler.processors.image_processor import ImageProcessor
from crawler.types import FetchResponse, LeaseResult, ProcessorResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_lease(url: str = "https://example.com/img.png") -> LeaseResult:
    return LeaseResult(
        normalized_url=url,
        url=url,
        depth=1,
        lease_token="token-img",
        lease_expires_at=9999999999999,
    )


def _make_response(
    body: bytes,
    content_type: str = "image/png",
    content_length: str | None = None,
) -> FetchResponse:
    headers = {"content-type": content_type}
    if content_length is not None:
        headers["content-length"] = content_length
    return FetchResponse(status_code=200, headers=headers, body=body)


def _make_valid_png() -> bytes:
    """Create a minimal valid 1x1 PNG image."""
    from PIL import Image

    buf = io.BytesIO()
    img = Image.new("RGBA", (100, 50), color=(255, 0, 0, 255))
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_valid_jpeg(width: int = 640, height: int = 480) -> bytes:
    """Create a valid JPEG image of given dimensions."""
    from PIL import Image

    buf = io.BytesIO()
    img = Image.new("RGB", (width, height), color=(0, 128, 255))
    img.save(buf, format="JPEG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Dimension extraction
# ---------------------------------------------------------------------------


class TestDimensionExtraction:
    """ImageProcessor extracts width/height from image bytes."""

    @pytest.mark.asyncio
    async def test_extracts_dimensions_from_valid_png(self) -> None:
        """Extracts correct width and height from a valid PNG."""
        body = _make_valid_png()  # 100x50
        proc = ImageProcessor()
        result = await proc.process(
            _make_response(body, "image/png"), _make_lease(), None
        )

        assert result.metadata["width"] == 100
        assert result.metadata["height"] == 50

    @pytest.mark.asyncio
    async def test_extracts_dimensions_from_valid_jpeg(self) -> None:
        """Extracts correct width and height from a valid JPEG."""
        body = _make_valid_jpeg(800, 600)
        proc = ImageProcessor()
        result = await proc.process(
            _make_response(body, "image/jpeg"), _make_lease(), None
        )

        assert result.metadata["width"] == 800
        assert result.metadata["height"] == 600

    @pytest.mark.asyncio
    async def test_returns_none_dimensions_for_corrupt_image(self) -> None:
        """Corrupt bytes → width=None, height=None (no crash)."""
        body = b"\x00\x01\x02\x03\x04 not an image"
        proc = ImageProcessor()
        result = await proc.process(
            _make_response(body, "image/png"), _make_lease(), None
        )

        assert result.metadata["width"] is None
        assert result.metadata["height"] is None

    @pytest.mark.asyncio
    async def test_returns_none_dimensions_for_empty_body(self) -> None:
        """Empty body → null dimensions."""
        proc = ImageProcessor()
        result = await proc.process(
            _make_response(b"", "image/png"), _make_lease(), None
        )

        assert result.metadata["width"] is None
        assert result.metadata["height"] is None


# ---------------------------------------------------------------------------
# File size
# ---------------------------------------------------------------------------


class TestFileSize:
    """ImageProcessor records file_size_bytes from header or body length."""

    @pytest.mark.asyncio
    async def test_file_size_from_content_length_header(self) -> None:
        """Uses Content-Length header value when present."""
        body = _make_valid_png()
        proc = ImageProcessor()
        result = await proc.process(
            _make_response(body, "image/png", content_length="12345"),
            _make_lease(),
            None,
        )

        assert result.metadata["file_size_bytes"] == 12345

    @pytest.mark.asyncio
    async def test_file_size_from_body_length_when_no_header(self) -> None:
        """Falls back to len(body) when Content-Length is absent."""
        body = _make_valid_png()
        proc = ImageProcessor()
        result = await proc.process(
            _make_response(body, "image/png", content_length=None),
            _make_lease(),
            None,
        )

        assert result.metadata["file_size_bytes"] == len(body)


# ---------------------------------------------------------------------------
# Content hash
# ---------------------------------------------------------------------------


class TestContentHash:
    """ImageProcessor computes SHA-256 hash of response body."""

    @pytest.mark.asyncio
    async def test_content_hash_is_sha256(self) -> None:
        body = _make_valid_png()
        expected = hashlib.sha256(body).hexdigest()
        proc = ImageProcessor()
        result = await proc.process(
            _make_response(body, "image/png"), _make_lease(), None
        )

        assert result.content_hash == expected


# ---------------------------------------------------------------------------
# File extension and path
# ---------------------------------------------------------------------------


class TestFileExtensionAndPath:
    """ImageProcessor determines extension from Content-Type."""

    @pytest.mark.asyncio
    async def test_png_extension(self) -> None:
        body = _make_valid_png()
        expected_hash = hashlib.sha256(body).hexdigest()
        proc = ImageProcessor()
        result = await proc.process(
            _make_response(body, "image/png"), _make_lease(), None
        )

        assert result.file_path == f"output/images/{expected_hash}.png"

    @pytest.mark.asyncio
    async def test_jpeg_extension(self) -> None:
        body = _make_valid_jpeg()
        expected_hash = hashlib.sha256(body).hexdigest()
        proc = ImageProcessor()
        result = await proc.process(
            _make_response(body, "image/jpeg"), _make_lease(), None
        )

        assert result.file_path == f"output/images/{expected_hash}.jpeg"

    @pytest.mark.asyncio
    async def test_fallback_bin_extension_for_unknown_type(self) -> None:
        """Unknown image subtype → 'bin' extension."""
        body = b"\x89PNG fake"
        expected_hash = hashlib.sha256(body).hexdigest()
        proc = ImageProcessor()
        result = await proc.process(
            _make_response(body, "image/x-unknown-format"), _make_lease(), None
        )

        assert result.file_path == f"output/images/{expected_hash}.bin"


# ---------------------------------------------------------------------------
# No discovered URLs
# ---------------------------------------------------------------------------


class TestNoDiscoveredUrls:
    """Images don't contain links — discovered_urls is always empty."""

    @pytest.mark.asyncio
    async def test_discovered_urls_is_empty(self) -> None:
        body = _make_valid_png()
        proc = ImageProcessor()
        result = await proc.process(
            _make_response(body, "image/png"), _make_lease(), None
        )

        assert result.discovered_urls == []
