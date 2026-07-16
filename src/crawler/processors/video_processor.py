"""Video content processor — extracts metadata, detects truncation, persists video files.

Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 12.6, 12.7
"""

from crawler.content_dispatcher import BaseProcessor
from crawler.types import (
    FetchResponse,
    LeaseResult,
    ProcessorResult,
    TransientError,
    VideoMetadata,
)


# ---------------------------------------------------------------------------
# MIME type → file extension mapping
# ---------------------------------------------------------------------------

_MIME_TO_EXT: dict[str, str] = {
    "video/mp4": "mp4",
    "video/webm": "webm",
    "video/ogg": "ogg",
    "video/mpeg": "mpeg",
    "video/quicktime": "mov",
    "video/x-msvideo": "avi",
    "video/x-matroska": "mkv",
    "video/x-flv": "flv",
    "video/3gpp": "3gp",
    "video/3gpp2": "3g2",
    "video/x-ms-wmv": "wmv",
}


def _mime_to_extension(content_type: str) -> str:
    """Derive a file extension from a Content-Type header value.

    Strips parameters (e.g. '; charset=utf-8') and looks up the MIME type
    in the known mapping. Falls back to 'bin' for unknown types.
    """
    mime = content_type.split(";")[0].strip().lower()
    return _MIME_TO_EXT.get(mime, "bin")


class VideoProcessor(BaseProcessor):
    """Processes video/* responses: extracts file size, duration, persists raw video."""

    async def process(
        self, response: FetchResponse, lease: LeaseResult, store: object
    ) -> ProcessorResult:
        """Extract metadata, detect truncation, persist video, return result.

        Steps:
          1. Determine file size from Content-Length header or body length.
          2. Extract duration from X-Duration header (if present).
          3. Detect truncation: body length < Content-Length → TransientError.
          4. Compute content hash.
          5. Derive extension from Content-Type.
          6. Persist video to output/videos/<hash>.<ext>.
          7. Store metadata in MetadataStore.
          8. Return ProcessorResult.
        """
        body = response.body or b""

        # --- File size ---
        content_length_header = response.headers.get("content-length")
        if content_length_header is not None:
            try:
                declared_length = int(content_length_header)
            except (ValueError, TypeError):
                declared_length = None
        else:
            declared_length = None

        # Truncation detection: if Content-Length is present and body is shorter
        if declared_length is not None and len(body) < declared_length:
            raise TransientError("truncated download")

        file_size = declared_length if declared_length is not None else len(body)

        # --- Duration ---
        duration_header = response.headers.get("x-duration")
        duration_seconds: float | None = None
        if duration_header is not None:
            try:
                duration_seconds = float(duration_header)
            except (ValueError, TypeError):
                duration_seconds = None

        # --- Hash & extension ---
        content_hash = self.compute_hash(body)
        content_type = response.headers.get("content-type", "application/octet-stream")
        ext = _mime_to_extension(content_type)

        # --- Persist file ---
        file_path = f"{self._output_dir}/videos/{content_hash}.{ext}"
        await self.write_file_if_not_exists(file_path, body)

        # --- Store metadata ---
        video_metadata = VideoMetadata(
            file_size_bytes=file_size,
            duration_seconds=duration_seconds,
        )
        if store is not None:
            await store.store_video_metadata(  # type: ignore[attr-defined]
                normalized_url=lease.normalized_url,
                metadata=video_metadata,
            )

        # --- Return result ---
        return ProcessorResult(
            discovered_urls=[],
            metadata={
                "file_size_bytes": file_size,
                "duration_seconds": duration_seconds,
            },
            content_hash=content_hash,
            file_path=file_path,
        )
