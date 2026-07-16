"""
Unit tests for PdfProcessor (Task 7.7).

Tests:
- Extracts page count from valid PDF
- Extracts document title from PDF metadata
- Returns empty string title when metadata has no title
- Returns None page_count and None title for corrupt/invalid PDF
- Still persists raw file even on parse failure
- Computes SHA-256 content hash
- Persists to output/pdfs/<hash>.pdf
- discovered_urls is always empty
"""

import hashlib
import io

import pytest

from crawler.processors.pdf_processor import PdfProcessor
from crawler.types import FetchResponse, LeaseResult, ProcessorResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_lease(url: str = "https://example.com/doc.pdf") -> LeaseResult:
    return LeaseResult(
        normalized_url=url,
        url=url,
        depth=1,
        lease_token="token-pdf",
        lease_expires_at=9999999999999,
    )


def _make_response(body: bytes) -> FetchResponse:
    return FetchResponse(
        status_code=200,
        headers={"content-type": "application/pdf"},
        body=body,
    )


def _make_valid_pdf(page_count: int = 3, title: str = "Test Document") -> bytes:
    """Create a minimal valid PDF with given page count and title."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=612, height=792)

    writer.add_metadata({"/Title": title})

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _make_valid_pdf_no_title(page_count: int = 2) -> bytes:
    """Create a valid PDF with no title metadata."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=612, height=792)

    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Page count extraction
# ---------------------------------------------------------------------------


class TestPageCountExtraction:
    """PdfProcessor extracts page count from valid PDFs."""

    @pytest.mark.asyncio
    async def test_extracts_page_count_from_valid_pdf(self) -> None:
        """Page count matches the actual number of pages."""
        body = _make_valid_pdf(page_count=5)
        proc = PdfProcessor()
        result = await proc.process(_make_response(body), _make_lease(), None)

        assert result.metadata["page_count"] == 5

    @pytest.mark.asyncio
    async def test_single_page_pdf(self) -> None:
        """Single-page PDF → page_count = 1."""
        body = _make_valid_pdf(page_count=1)
        proc = PdfProcessor()
        result = await proc.process(_make_response(body), _make_lease(), None)

        assert result.metadata["page_count"] == 1


# ---------------------------------------------------------------------------
# Title extraction
# ---------------------------------------------------------------------------


class TestTitleExtraction:
    """PdfProcessor extracts document title from PDF metadata."""

    @pytest.mark.asyncio
    async def test_extracts_document_title(self) -> None:
        """Document title from metadata."""
        body = _make_valid_pdf(title="Annual Report 2024")
        proc = PdfProcessor()
        result = await proc.process(_make_response(body), _make_lease(), None)

        assert result.metadata["document_title"] == "Annual Report 2024"

    @pytest.mark.asyncio
    async def test_empty_title_when_metadata_has_no_title(self) -> None:
        """PDF without title in metadata → empty string."""
        body = _make_valid_pdf_no_title()
        proc = PdfProcessor()
        result = await proc.process(_make_response(body), _make_lease(), None)

        assert result.metadata["document_title"] == ""


# ---------------------------------------------------------------------------
# Parse failure handling
# ---------------------------------------------------------------------------


class TestParseFailure:
    """PdfProcessor handles corrupt/invalid PDFs gracefully."""

    @pytest.mark.asyncio
    async def test_null_metadata_for_corrupt_pdf(self) -> None:
        """Corrupt bytes → page_count=None, document_title=None."""
        body = b"not a pdf at all \x00\x01\x02"
        proc = PdfProcessor()
        result = await proc.process(_make_response(body), _make_lease(), None)

        assert result.metadata["page_count"] is None
        assert result.metadata["document_title"] is None

    @pytest.mark.asyncio
    async def test_still_persists_file_on_parse_failure(self) -> None:
        """Even on parse failure, file_path is set (raw file persisted)."""
        body = b"garbage pdf content"
        expected_hash = hashlib.sha256(body).hexdigest()
        proc = PdfProcessor()
        result = await proc.process(_make_response(body), _make_lease(), None)

        assert result.file_path == f"output/pdfs/{expected_hash}.pdf"

    @pytest.mark.asyncio
    async def test_empty_body_returns_null_metadata(self) -> None:
        """Empty body → null metadata, still produces a result."""
        proc = PdfProcessor()
        result = await proc.process(_make_response(b""), _make_lease(), None)

        assert result.metadata["page_count"] is None
        assert result.metadata["document_title"] is None


# ---------------------------------------------------------------------------
# Content hash
# ---------------------------------------------------------------------------


class TestContentHash:
    """PdfProcessor computes SHA-256 hash of response body."""

    @pytest.mark.asyncio
    async def test_content_hash_is_sha256(self) -> None:
        body = _make_valid_pdf()
        expected = hashlib.sha256(body).hexdigest()
        proc = PdfProcessor()
        result = await proc.process(_make_response(body), _make_lease(), None)

        assert result.content_hash == expected


# ---------------------------------------------------------------------------
# File path
# ---------------------------------------------------------------------------


class TestFilePath:
    """PdfProcessor persists to output/pdfs/<hash>.pdf."""

    @pytest.mark.asyncio
    async def test_file_path_format(self) -> None:
        body = _make_valid_pdf()
        expected_hash = hashlib.sha256(body).hexdigest()
        proc = PdfProcessor()
        result = await proc.process(_make_response(body), _make_lease(), None)

        assert result.file_path == f"output/pdfs/{expected_hash}.pdf"


# ---------------------------------------------------------------------------
# No discovered URLs
# ---------------------------------------------------------------------------


class TestNoDiscoveredUrls:
    """PDFs don't contain crawlable links — discovered_urls is always empty."""

    @pytest.mark.asyncio
    async def test_discovered_urls_is_empty(self) -> None:
        body = _make_valid_pdf()
        proc = PdfProcessor()
        result = await proc.process(_make_response(body), _make_lease(), None)

        assert result.discovered_urls == []
