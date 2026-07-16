"""
Unit tests for ContentDispatcher and BaseProcessor (Task 7.1).

Tests:
- register() stores processors keyed by MIME prefix
- dispatch() exact match takes priority over prefix match
- dispatch() prefix match works for subtypes (image/png → "image/")
- dispatch() returns None for unsupported content types
- process() extracts content-type from headers and dispatches
- process() returns None for unsupported types
- BaseProcessor.compute_hash() returns SHA-256 hex digest
- BaseProcessor.write_file_if_not_exists() only writes if file absent
"""

import hashlib
import os
from unittest.mock import AsyncMock, patch

import pytest

from crawler.content_dispatcher import BaseProcessor, ContentDispatcher
from crawler.types import FetchResponse, LeaseResult, ProcessorResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeProcessor(BaseProcessor):
    """Concrete processor for testing dispatch routing."""

    def __init__(self, name: str = "fake") -> None:
        self.name = name
        self.process_called_with = None

    async def process(self, response, lease, store) -> ProcessorResult:
        self.process_called_with = (response, lease, store)
        return ProcessorResult(
            discovered_urls=[],
            metadata={"processor": self.name},
            content_hash="fakehash",
            file_path=f"output/{self.name}/test.bin",
        )


def _make_lease() -> LeaseResult:
    return LeaseResult(
        normalized_url="https://example.com/page",
        url="https://example.com/page",
        depth=0,
        lease_token="token",
        lease_expires_at=9999999999999,
    )


def _make_response(content_type: str, body: bytes = b"data") -> FetchResponse:
    return FetchResponse(
        status_code=200,
        headers={"content-type": content_type},
        body=body,
    )


# ---------------------------------------------------------------------------
# register() and dispatch()
# ---------------------------------------------------------------------------


class TestRegisterAndDispatch:
    """ContentDispatcher.register() and dispatch() routing logic."""

    def test_dispatch_exact_match(self) -> None:
        """Exact MIME type match returns the registered processor."""
        dispatcher = ContentDispatcher()
        html_proc = FakeProcessor("html")
        dispatcher.register("text/html", html_proc)

        result = dispatcher.dispatch("text/html")
        assert result is html_proc

    def test_dispatch_prefix_match(self) -> None:
        """Prefix match works for subtypes (e.g., image/png matches 'image/')."""
        dispatcher = ContentDispatcher()
        img_proc = FakeProcessor("image")
        dispatcher.register("image/", img_proc)

        result = dispatcher.dispatch("image/png")
        assert result is img_proc

    def test_dispatch_exact_match_takes_priority(self) -> None:
        """Exact match is preferred over prefix match."""
        dispatcher = ContentDispatcher()
        general_proc = FakeProcessor("general")
        specific_proc = FakeProcessor("specific")

        dispatcher.register("text/", general_proc)
        dispatcher.register("text/html", specific_proc)

        result = dispatcher.dispatch("text/html")
        assert result is specific_proc

    def test_dispatch_returns_none_for_unsupported(self) -> None:
        """Unsupported content type → None."""
        dispatcher = ContentDispatcher()
        dispatcher.register("text/html", FakeProcessor())

        result = dispatcher.dispatch("application/octet-stream")
        assert result is None

    def test_dispatch_empty_registry_returns_none(self) -> None:
        """With no registered processors, always returns None."""
        dispatcher = ContentDispatcher()
        result = dispatcher.dispatch("text/html")
        assert result is None

    def test_dispatch_video_prefix(self) -> None:
        """video/* matches 'video/' prefix."""
        dispatcher = ContentDispatcher()
        vid_proc = FakeProcessor("video")
        dispatcher.register("video/", vid_proc)

        assert dispatcher.dispatch("video/mp4") is vid_proc
        assert dispatcher.dispatch("video/webm") is vid_proc

    def test_dispatch_application_pdf_exact(self) -> None:
        """application/pdf exact match."""
        dispatcher = ContentDispatcher()
        pdf_proc = FakeProcessor("pdf")
        dispatcher.register("application/pdf", pdf_proc)

        assert dispatcher.dispatch("application/pdf") is pdf_proc
        assert dispatcher.dispatch("application/json") is None

    def test_dispatch_content_type_with_charset_suffix(self) -> None:
        """Content-Type with charset (text/html; charset=utf-8) matches text/html."""
        dispatcher = ContentDispatcher()
        html_proc = FakeProcessor("html")
        dispatcher.register("text/html", html_proc)

        # "text/html; charset=utf-8" starts with "text/html"
        result = dispatcher.dispatch("text/html; charset=utf-8")
        assert result is html_proc


# ---------------------------------------------------------------------------
# process() — Full dispatch + processing flow
# ---------------------------------------------------------------------------


class TestProcess:
    """ContentDispatcher.process() dispatches and invokes the processor."""

    @pytest.mark.asyncio
    async def test_process_dispatches_to_correct_processor(self) -> None:
        """process() finds the right processor and calls its process method."""
        dispatcher = ContentDispatcher()
        html_proc = FakeProcessor("html")
        dispatcher.register("text/html", html_proc)

        response = _make_response("text/html")
        lease = _make_lease()

        result = await dispatcher.process(response, lease, store=None)

        assert result is not None
        assert result.metadata == {"processor": "html"}
        assert html_proc.process_called_with is not None

    @pytest.mark.asyncio
    async def test_process_returns_none_for_unsupported_type(self) -> None:
        """process() returns None when no processor matches."""
        dispatcher = ContentDispatcher()
        dispatcher.register("text/html", FakeProcessor())

        response = _make_response("application/xml")
        result = await dispatcher.process(response, _make_lease(), store=None)

        assert result is None

    @pytest.mark.asyncio
    async def test_process_extracts_content_type_from_headers(self) -> None:
        """process() reads content-type from response.headers."""
        dispatcher = ContentDispatcher()
        img_proc = FakeProcessor("image")
        dispatcher.register("image/", img_proc)

        response = _make_response("image/jpeg")
        await dispatcher.process(response, _make_lease(), store=None)

        assert img_proc.process_called_with is not None

    @pytest.mark.asyncio
    async def test_process_handles_missing_content_type_header(self) -> None:
        """Missing content-type header → returns None (no processor matches '')."""
        dispatcher = ContentDispatcher()
        dispatcher.register("text/html", FakeProcessor())

        response = FetchResponse(status_code=200, headers={}, body=b"data")
        result = await dispatcher.process(response, _make_lease(), store=None)

        assert result is None


# ---------------------------------------------------------------------------
# BaseProcessor.compute_hash() — SHA-256
# ---------------------------------------------------------------------------


class TestComputeHash:
    """BaseProcessor.compute_hash() returns SHA-256 hex digest."""

    def test_compute_hash_returns_sha256(self) -> None:
        """Hash matches hashlib.sha256 output."""
        proc = FakeProcessor()
        body = b"hello world"
        expected = hashlib.sha256(body).hexdigest()

        result = proc.compute_hash(body)

        assert result == expected
        assert len(result) == 64  # 256 bits = 64 hex chars

    def test_compute_hash_is_deterministic(self) -> None:
        """Same input always produces same hash."""
        proc = FakeProcessor()
        body = b"deterministic input"

        h1 = proc.compute_hash(body)
        h2 = proc.compute_hash(body)

        assert h1 == h2

    def test_compute_hash_different_for_different_bodies(self) -> None:
        """Different inputs produce different hashes."""
        proc = FakeProcessor()

        h1 = proc.compute_hash(b"body one")
        h2 = proc.compute_hash(b"body two")

        assert h1 != h2

    def test_compute_hash_empty_body(self) -> None:
        """Empty bytes still produces a valid hash."""
        proc = FakeProcessor()
        result = proc.compute_hash(b"")
        assert result == hashlib.sha256(b"").hexdigest()
        assert len(result) == 64


# ---------------------------------------------------------------------------
# BaseProcessor.write_file_if_not_exists()
# ---------------------------------------------------------------------------


class TestWriteFileIfNotExists:
    """BaseProcessor.write_file_if_not_exists() only writes new files."""

    @pytest.mark.asyncio
    async def test_writes_file_when_not_exists(self, tmp_path) -> None:
        """Creates the file when it doesn't exist."""
        proc = FakeProcessor()
        file_path = str(tmp_path / "new_file.bin")
        body = b"file content"

        await proc.write_file_if_not_exists(file_path, body)

        assert os.path.exists(file_path)
        with open(file_path, "rb") as f:
            assert f.read() == body

    @pytest.mark.asyncio
    async def test_does_not_overwrite_existing_file(self, tmp_path) -> None:
        """Does NOT overwrite an already-existing file."""
        proc = FakeProcessor()
        file_path = str(tmp_path / "existing.bin")

        # Pre-create file with different content
        with open(file_path, "wb") as f:
            f.write(b"original content")

        await proc.write_file_if_not_exists(file_path, b"new content")

        with open(file_path, "rb") as f:
            assert f.read() == b"original content"  # unchanged
