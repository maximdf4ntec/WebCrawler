"""Content dispatcher: routes fetched responses to type-specific processors."""

from __future__ import annotations

import hashlib
import os
from abc import ABC, abstractmethod
from typing import Optional

from crawler.types import FetchResponse, LeaseResult, ProcessorResult


class BaseProcessor(ABC):
    """Abstract base class for all content type processors."""

    def __init__(self, output_dir: str = "output") -> None:
        """Initialize the processor.

        Args:
            output_dir: Base directory for persisting content files.
        """
        self._output_dir = output_dir

    @abstractmethod
    async def process(
        self, response: FetchResponse, lease: LeaseResult, store: object
    ) -> ProcessorResult:
        """Process a fetched response and return extracted metadata + discovered URLs."""
        ...

    def compute_hash(self, body: bytes) -> str:
        """Compute SHA-256 content hash of raw body bytes."""
        return hashlib.sha256(body).hexdigest()

    async def write_file_if_not_exists(self, file_path: str, body: bytes) -> None:
        """Persist content to disk only if the file doesn't already exist.

        Creates parent directories as needed. Uses async file I/O via aiofiles.
        """
        import aiofiles

        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        if not os.path.exists(file_path):
            async with aiofiles.open(file_path, "wb") as f:
                await f.write(body)


class ContentDispatcher:
    """Dispatcher that routes responses to registered type-specific processors.

    Processor table is keyed by MIME type or MIME prefix (e.g. "text/html" or
    "image/"). Dispatch does exact match first, then falls back to prefix match.
    """

    def __init__(self, output_dir: str = "output") -> None:
        self._processors: dict[str, BaseProcessor] = {}
        self._output_dir = output_dir

    def register(self, mime_prefix: str, processor: BaseProcessor) -> None:
        """Register a processor for a given MIME type or prefix."""
        self._processors[mime_prefix] = processor

    def dispatch(self, content_type: str) -> Optional[BaseProcessor]:
        """Find the matching processor. Exact match first, then prefix match.

        Returns None if no registered processor handles the given content type,
        signalling the caller to mark the URL as Terminal_Failed.
        """
        # Strip parameters (e.g. "; charset=utf-8") from content-type
        mime = content_type.split(";")[0].strip().lower()

        # 1) Exact match takes priority
        if mime in self._processors:
            return self._processors[mime]

        # 2) Prefix match (e.g. "image/" matches "image/png")
        for key, handler in self._processors.items():
            if mime.startswith(key):
                return handler

        return None

    async def process(
        self, response: FetchResponse, lease: LeaseResult, store: object
    ) -> Optional[ProcessorResult]:
        """Dispatch and process a response. Returns None if unsupported type."""
        content_type = response.headers.get("content-type", "")
        processor = self.dispatch(content_type)
        if processor is None:
            return None
        return await processor.process(response, lease, store)
