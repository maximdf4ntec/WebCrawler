"""PDF content processor: extracts page count, title, and persists raw PDF.

Requirements: 13.1, 13.2, 13.3, 13.4, 13.5
"""

import asyncio
import io
from typing import Optional

from pypdf import PdfReader

from crawler.content_dispatcher import BaseProcessor
from crawler.logger import get_logger
from crawler.types import FetchResponse, LeaseResult, PdfMetadata, ProcessorResult

logger = get_logger()


class PdfProcessor(BaseProcessor):
    """Processes application/pdf responses: extracts page count, title, persists raw PDF."""

    async def process(
        self, response: FetchResponse, lease: LeaseResult, store: object
    ) -> ProcessorResult:
        """Parse PDF metadata, persist file, store metadata, return result.

        CPU-bound PDF parsing is offloaded to a thread pool via asyncio.to_thread().
        On parse failure, page_count and document_title are set to None but the
        raw file is still persisted and metadata is still stored.

        Args:
            response: The fetched HTTP response containing PDF body bytes.
            lease: The lease information for the URL being processed.
            store: The MetadataStore instance for persisting metadata.

        Returns:
            ProcessorResult with empty discovered_urls, metadata dict, hash, and file path.
        """
        body = response.body or b""
        content_hash = await asyncio.to_thread(self.compute_hash, body)

        # CPU-bound PDF parsing offloaded to thread pool
        page_count, document_title = await asyncio.to_thread(
            self._parse_pdf, body, lease.url
        )

        file_path = f"{self._output_dir}/pdfs/{content_hash}.pdf"
        await self.write_file_if_not_exists(file_path, body)

        # Store metadata in MetadataStore (if store is available)
        if store is not None:
            await store.store_pdf_metadata(
                normalized_url=lease.normalized_url,
                page_count=page_count,
                document_title=document_title,
            )

        return ProcessorResult(
            discovered_urls=[],
            metadata={"page_count": page_count, "document_title": document_title},
            content_hash=content_hash,
            file_path=file_path,
        )

    def _parse_pdf(self, body: bytes, url: str) -> tuple[Optional[int], Optional[str]]:
        """Synchronous PDF parsing, run in a thread.

        Args:
            body: Raw PDF bytes.
            url: The URL of the PDF (used for logging on failure).

        Returns:
            Tuple of (page_count, document_title). Both are None on parse failure.
        """
        try:
            reader = PdfReader(io.BytesIO(body))
            page_count = len(reader.pages)
            document_title = (
                reader.metadata.title
                if reader.metadata and reader.metadata.title
                else ""
            )
            return (page_count, document_title)
        except Exception as e:
            logger.warn(
                "pdf_parse_failed",
                url=url,
                error_message=str(e),
            )
            return (None, None)
