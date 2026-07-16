# Stub — implementation pending (Task 7.7)
from crawler.content_dispatcher import BaseProcessor
from crawler.types import FetchResponse, LeaseResult, ProcessorResult


class PdfProcessor(BaseProcessor):
    """Processes application/pdf responses: extracts page count, title, persists raw PDF."""

    async def process(
        self, response: FetchResponse, lease: LeaseResult, store: object
    ) -> ProcessorResult:
        """Parse PDF metadata, persist file, return result."""
        raise NotImplementedError
