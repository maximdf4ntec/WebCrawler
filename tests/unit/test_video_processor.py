"""
Unit tests for VideoProcessor (Task 7.6).

Tests:
- Records file_size_bytes from Content-Length header
- Falls back to len(body) when Content-Length absent
- Detects truncation (body < Content-Length) → raises TransientError
- Extracts duration from X-Duration header as float
- Returns None duration when X-Duration absent
- Computes SHA-256 content hash
- Determines file extension from Content-Type, fallback "bin"
- Persists to output/videos/<hash>.<ext>
- discovered_urls is always empty
"""

import hashlib

import pytest

from crawler.processors.video_processor import VideoProcessor
from crawler.types import FetchResponse, LeaseResult, ProcessorResult, TransientError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_lease(url: str = "https://example.com/vid.mp4") -> LeaseResult:
    return LeaseResult(
        normalized_url=url,
        url=url,
        depth=1,
        lease_token="token-vid",
        lease_expires_at=9999999999999,
    )


def _make_response(
    body: bytes,
    content_type: str = "video/mp4",
    content_length: str | None = None,
    x_duration: str | None = None,
) -> FetchResponse:
    headers: dict[str, str] = {"content-type": content_type}
    if content_length is not None:
        headers["content-length"] = content_length
    if x_duration is not None:
        headers["x-duration"] = x_duration
    return FetchResponse(status_code=200, headers=headers, body=body)


# ---------------------------------------------------------------------------
# File size
# ---------------------------------------------------------------------------


class TestFileSize:
    """VideoProcessor records file_size_bytes."""

    @pytest.mark.asyncio
    async def test_file_size_from_content_length_header(self) -> None:
        """Uses Content-Length header value when present."""
        body = b"x" * 500
        proc = VideoProcessor()
        result = await proc.process(
            _make_response(body, content_length="500"), _make_lease(), None
        )

        assert result.metadata["file_size_bytes"] == 500

    @pytest.mark.asyncio
    async def test_file_size_from_body_length_when_no_header(self) -> None:
        """Falls back to len(body) when Content-Length is absent."""
        body = b"x" * 2048
        proc = VideoProcessor()
        result = await proc.process(_make_response(body), _make_lease(), None)

        assert result.metadata["file_size_bytes"] == 2048


# ---------------------------------------------------------------------------
# Truncation detection
# ---------------------------------------------------------------------------


class TestTruncationDetection:
    """VideoProcessor raises TransientError on truncated download."""

    @pytest.mark.asyncio
    async def test_raises_transient_error_when_body_shorter_than_content_length(
        self,
    ) -> None:
        """body < Content-Length → TransientError("truncated download")."""
        body = b"x" * 50  # only 50 bytes
        proc = VideoProcessor()

        with pytest.raises(TransientError, match="truncated download"):
            await proc.process(
                _make_response(body, content_length="100"),  # claims 100 bytes
                _make_lease(),
                None,
            )

    @pytest.mark.asyncio
    async def test_no_error_when_body_equals_content_length(self) -> None:
        """body == Content-Length → no truncation error."""
        body = b"x" * 100
        proc = VideoProcessor()
        result = await proc.process(
            _make_response(body, content_length="100"), _make_lease(), None
        )

        assert result.metadata["file_size_bytes"] == 100

    @pytest.mark.asyncio
    async def test_no_error_when_content_length_absent(self) -> None:
        """No Content-Length header → truncation check skipped."""
        body = b"x" * 50
        proc = VideoProcessor()
        result = await proc.process(_make_response(body), _make_lease(), None)

        assert result.metadata["file_size_bytes"] == 50


# ---------------------------------------------------------------------------
# Duration extraction
# ---------------------------------------------------------------------------


class TestDurationExtraction:
    """VideoProcessor extracts duration from X-Duration header."""

    @pytest.mark.asyncio
    async def test_extracts_duration_from_x_duration_header(self) -> None:
        """X-Duration header → float duration_seconds."""
        body = b"x" * 100
        proc = VideoProcessor()
        result = await proc.process(
            _make_response(body, x_duration="125.5"), _make_lease(), None
        )

        assert result.metadata["duration_seconds"] == pytest.approx(125.5)

    @pytest.mark.asyncio
    async def test_duration_none_when_header_absent(self) -> None:
        """No X-Duration header → duration_seconds is None."""
        body = b"x" * 100
        proc = VideoProcessor()
        result = await proc.process(_make_response(body), _make_lease(), None)

        assert result.metadata["duration_seconds"] is None


# ---------------------------------------------------------------------------
# Content hash
# ---------------------------------------------------------------------------


class TestContentHash:
    """VideoProcessor computes SHA-256 hash of response body."""

    @pytest.mark.asyncio
    async def test_content_hash_is_sha256(self) -> None:
        body = b"video bytes here"
        expected = hashlib.sha256(body).hexdigest()
        proc = VideoProcessor()
        result = await proc.process(
            _make_response(body, content_length=str(len(body))), _make_lease(), None
        )

        assert result.content_hash == expected


# ---------------------------------------------------------------------------
# File extension and path
# ---------------------------------------------------------------------------


class TestFileExtensionAndPath:
    """VideoProcessor determines extension from Content-Type."""

    @pytest.mark.asyncio
    async def test_mp4_extension(self) -> None:
        body = b"fake mp4"
        expected_hash = hashlib.sha256(body).hexdigest()
        proc = VideoProcessor()
        result = await proc.process(
            _make_response(body, "video/mp4", content_length=str(len(body))),
            _make_lease(),
            None,
        )

        assert result.file_path == f"output/videos/{expected_hash}.mp4"

    @pytest.mark.asyncio
    async def test_webm_extension(self) -> None:
        body = b"fake webm"
        expected_hash = hashlib.sha256(body).hexdigest()
        proc = VideoProcessor()
        result = await proc.process(
            _make_response(body, "video/webm", content_length=str(len(body))),
            _make_lease(),
            None,
        )

        assert result.file_path == f"output/videos/{expected_hash}.webm"

    @pytest.mark.asyncio
    async def test_fallback_bin_extension(self) -> None:
        """Unknown video subtype → 'bin' extension."""
        body = b"mystery video"
        expected_hash = hashlib.sha256(body).hexdigest()
        proc = VideoProcessor()
        result = await proc.process(
            _make_response(body, "video/x-unknown", content_length=str(len(body))),
            _make_lease(),
            None,
        )

        assert result.file_path == f"output/videos/{expected_hash}.bin"


# ---------------------------------------------------------------------------
# No discovered URLs
# ---------------------------------------------------------------------------


class TestNoDiscoveredUrls:
    """Videos don't contain links — discovered_urls is always empty."""

    @pytest.mark.asyncio
    async def test_discovered_urls_is_empty(self) -> None:
        body = b"video data"
        proc = VideoProcessor()
        result = await proc.process(
            _make_response(body, content_length=str(len(body))), _make_lease(), None
        )

        assert result.discovered_urls == []
