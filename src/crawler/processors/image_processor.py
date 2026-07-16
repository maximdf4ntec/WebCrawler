# Stub — implementation pending (Task 7.5)
from crawler.content_dispatcher import BaseProcessor
from crawler.types import FetchResponse, LeaseResult, ProcessorResult


class ImageProcessor(BaseProcessor):
    """Processes image/* responses: extracts dimensions, file size, persists raw image."""

    async def process(
        self, response: FetchResponse, lease: LeaseResult, store: object
    ) -> ProcessorResult:
        """Extract dimensions, persist image, return result."""
        raise NotImplementedError
