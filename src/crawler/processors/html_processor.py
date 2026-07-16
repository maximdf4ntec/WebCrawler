# Stub — implementation pending (Task 7.3)
from crawler.content_dispatcher import BaseProcessor
from crawler.types import FetchResponse, LeaseResult, ProcessorResult


class HtmlProcessor(BaseProcessor):
    """Processes text/html responses: extracts links, title, and persists raw HTML."""

    async def process(
        self, response: FetchResponse, lease: LeaseResult, store: object
    ) -> ProcessorResult:
        """Parse HTML, extract links/title, persist, return result."""
        raise NotImplementedError
