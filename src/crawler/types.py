"""Core types, Pydantic models, enums, and exception classes for the web crawler."""

from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class CrawlState(str, Enum):
    """State of a URL in the crawl frontier."""

    Pending = "Pending"
    In_Progress = "In_Progress"
    Completed = "Completed"
    Retry = "Retry"
    Failed = "Failed"
    Terminal_Failed = "Terminal_Failed"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class CrawlerConfig(BaseModel):
    """Configuration for a crawl session, loadable from a YAML file."""

    seed_url: str
    max_depth: Optional[int] = None  # default: unlimited, range: 1–1000
    max_concurrency: int = 5  # range: 1–100
    max_retries: int = 3  # range: 0–10
    max_content_size: int = 50 * 1024 * 1024  # default: 50 MB, range: 1 KB–1 GB
    max_redirects: int = 5  # default: 5, max redirect chain length
    include_patterns: list[str] = Field(default_factory=list)
    exclude_patterns: list[str] = Field(default_factory=list)
    lease_timeout_ms: int = 60_000  # default: 60 s
    batch_size: int = 50  # range: 1–500
    progress_interval_ms: int = 10_000  # default: 10 s

    @classmethod
    def from_yaml(cls, path: Path) -> "CrawlerConfig":
        """Load configuration from a YAML file."""
        import yaml

        with open(path) as f:
            data = yaml.safe_load(f)
        return cls(**data)


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------


class CrawlResult(BaseModel):
    """Summary statistics returned when a crawl completes."""

    total_discovered: int
    total_completed: int
    total_failed: int
    total_terminal_failed: int
    duration_ms: int


class LeaseResult(BaseModel):
    """A leased URL record dispatched to a worker for processing."""

    normalized_url: str
    url: str
    depth: int
    lease_token: str
    lease_expires_at: int  # Unix timestamp ms


class WorkerResult(BaseModel):
    """Outcome reported by a worker after processing a single URL."""

    normalized_url: str
    status: str  # 'completed' | 'retry' | 'terminal_failed'
    content_hash: Optional[str] = None
    content_type: Optional[str] = None
    metadata: Optional[dict] = None
    discovered_urls: list[str] = Field(default_factory=list)
    failure_reason: Optional[str] = None


class FetchResponse(BaseModel):
    """Response from the HTTP fetch layer."""

    model_config = {"arbitrary_types_allowed": True}

    status_code: int
    headers: dict[str, str]
    body: Optional[bytes] = None


class ProcessorResult(BaseModel):
    """Result from a content processor (HTML, image, video, PDF)."""

    discovered_urls: list[str] = Field(default_factory=list)
    metadata: dict
    content_hash: str
    file_path: str


# ---------------------------------------------------------------------------
# Metadata models (content-type specific)
# ---------------------------------------------------------------------------


class HtmlMetadata(BaseModel):
    """Metadata extracted from an HTML page."""

    page_title: str
    link_count: int


class ImageMetadata(BaseModel):
    """Metadata extracted from an image file."""

    width: Optional[int] = None
    height: Optional[int] = None
    file_size_bytes: int


class VideoMetadata(BaseModel):
    """Metadata extracted from a video file."""

    file_size_bytes: int
    duration_seconds: Optional[float] = None


class PdfMetadata(BaseModel):
    """Metadata extracted from a PDF document."""

    page_count: Optional[int] = None
    document_title: Optional[str] = None


# ---------------------------------------------------------------------------
# Exception classes
# ---------------------------------------------------------------------------


class TransientError(Exception):
    """Recoverable error (network timeout, HTTP 500, 429 exhaustion, truncated download).

    The crawler should retry the request according to its retry policy.
    """


class PermanentError(Exception):
    """Non-recoverable error (HTTP 404, 403, malformed response, unsupported content type).

    The URL should be marked as terminally failed without retry.
    """


class QueueOverflowError(Exception):
    """Raised when the rate limiter queue is at capacity."""


class RateLimitExhaustedError(Exception):
    """Raised after 10 consecutive HTTP 429 responses."""
