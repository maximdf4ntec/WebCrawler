"""Image processor: extracts dimensions, file size, and persists raw image data.

Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6
"""

from __future__ import annotations

import asyncio
import io
import logging
from typing import Optional, Tuple

from PIL import Image

from crawler.content_dispatcher import BaseProcessor
from crawler.types import FetchResponse, ImageMetadata, LeaseResult, ProcessorResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MIME_TO_EXT: dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/jpg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/bmp": "bmp",
    "image/tiff": "tiff",
    "image/svg+xml": "svg",
    "image/x-icon": "ico",
    "image/vnd.microsoft.icon": "ico",
    "image/avif": "avif",
    "image/heic": "heic",
    "image/heif": "heif",
}


def mime_to_extension(content_type: str) -> str:
    """Derive file extension from a Content-Type header value.

    Strips parameters (e.g. '; charset=...') before lookup.
    Falls back to 'bin' if the MIME type is not recognized.
    """
    mime = content_type.split(";")[0].strip().lower()
    return _MIME_TO_EXT.get(mime, "bin")


def _decode_dimensions(body: bytes) -> Tuple[Optional[int], Optional[int]]:
    """Extract (width, height) from image bytes. Returns (None, None) on failure."""
    if not body:
        return None, None
    try:
        img = Image.open(io.BytesIO(body))
        width, height = img.size
        return width, height
    except Exception:
        return None, None


# ---------------------------------------------------------------------------
# Processor
# ---------------------------------------------------------------------------


class ImageProcessor(BaseProcessor):
    """Processes image/* responses: extracts dimensions, file size, persists raw image."""

    async def process(
        self, response: FetchResponse, lease: LeaseResult, store: object
    ) -> ProcessorResult:
        """Extract dimensions, persist image, store metadata, return result.

        CPU-bound image decode is offloaded to a thread pool via asyncio.to_thread().
        """
        body = response.body or b""
        content_type = response.headers.get("content-type", "application/octet-stream")

        # --- Dimensions (offloaded to thread pool) ---
        width, height = await asyncio.to_thread(_decode_dimensions, body)

        if width is None and body:
            logger.warning(
                "image_decode_failed",
                extra={"url": lease.normalized_url},
            )

        # --- File size ---
        content_length = response.headers.get("content-length")
        if content_length is not None:
            file_size_bytes = int(content_length)
        else:
            file_size_bytes = len(body)

        # --- Content hash and file path ---
        content_hash = self.compute_hash(body)
        ext = mime_to_extension(content_type)
        file_path = f"output/images/{content_hash}.{ext}"

        # --- Persist raw image to disk ---
        await self.write_file_if_not_exists(file_path, body)

        # --- Store metadata ---
        image_meta = ImageMetadata(
            width=width,
            height=height,
            file_size_bytes=file_size_bytes,
        )
        if store is not None and hasattr(store, "store_image_metadata"):
            await store.store_image_metadata(
                normalized_url=lease.normalized_url,
                metadata=image_meta,
            )

        # --- Build result ---
        return ProcessorResult(
            discovered_urls=[],
            metadata={
                "width": width,
                "height": height,
                "file_size_bytes": file_size_bytes,
            },
            content_hash=content_hash,
            file_path=file_path,
        )
