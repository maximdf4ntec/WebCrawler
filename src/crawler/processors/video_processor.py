# Stub — implementation pending (Task 7.6)
from crawler.content_dispatcher import BaseProcessor
from crawler.types import FetchResponse, LeaseResult, ProcessorResult


class VideoProcessor(BaseProcessor):
    """Processes video/* responses: extracts file size, duration, persists raw video."""

    async def process(
        self, response: FetchResponse, lease: LeaseResult, store: object
    ) -> ProcessorResult:
        """Extract metadata, detect truncation, persist video, return result."""
        raise NotImplementedError
