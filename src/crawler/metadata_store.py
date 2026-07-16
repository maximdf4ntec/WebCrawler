"""Metadata Store: SQLite-backed persistence for crawl state and metadata.

Provides async database access via aiosqlite with WAL mode for concurrent
reads, atomic operations for frontier management, and content-type-specific
metadata storage.

Requirements: 16.1, 16.2, 16.3, 2.5
"""

import json
from typing import Optional

import aiosqlite

from crawler.types import (
    CrawlerConfig,
    LeaseResult,
    HtmlMetadata,
    ImageMetadata,
    VideoMetadata,
    PdfMetadata,
)


class MetadataStore:
    """Async SQLite-backed store for crawl state, frontier, and metadata.

    Attributes:
        db_path: Path to the SQLite database file (or ":memory:" for tests).
    """

    def __init__(self, db_path: str = "crawl.db") -> None:
        self._db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None

    @property
    def db(self) -> aiosqlite.Connection:
        """Return the active database connection (raises if not initialized)."""
        if self._db is None:
            raise RuntimeError("MetadataStore not initialized. Call init() first.")
        return self._db

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    async def init(self) -> None:
        """Open the database connection, set pragmas, and create schema."""
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row

        # Configure SQLite pragmas for durability and performance
        await self._db.execute("PRAGMA journal_mode = WAL")
        await self._db.execute("PRAGMA busy_timeout = 5000")
        await self._db.execute("PRAGMA synchronous = NORMAL")
        await self._db.execute("PRAGMA foreign_keys = ON")

        await self._create_schema()

    async def _create_schema(self) -> None:
        """Create all tables and indexes if they don't exist."""
        await self._db.executescript(_SCHEMA_SQL)

    async def close(self) -> None:
        """Close the database connection."""
        if self._db is not None:
            await self._db.close()
            self._db = None

    # ------------------------------------------------------------------
    # Configuration persistence (Requirement 16.3)
    # ------------------------------------------------------------------

    async def store_config(self, config: CrawlerConfig, seed_domain: str) -> None:
        """Persist crawl configuration to the database.

        Uses INSERT OR REPLACE to ensure only one config row (id=1) exists.

        Args:
            config: The crawler configuration to persist.
            seed_domain: The extracted seed domain (host:port or host).
        """
        await self.db.execute(
            """
            INSERT OR REPLACE INTO crawl_config (
                id, seed_url, seed_domain, max_depth, max_concurrency,
                max_retries, max_content_size, max_redirects,
                lease_timeout_ms, batch_size, progress_interval_ms,
                include_patterns, exclude_patterns
            ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                config.seed_url,
                seed_domain,
                config.max_depth,
                config.max_concurrency,
                config.max_retries,
                config.max_content_size,
                config.max_redirects,
                config.lease_timeout_ms,
                config.batch_size,
                config.progress_interval_ms,
                json.dumps(config.include_patterns),
                json.dumps(config.exclude_patterns),
            ),
        )
        await self.db.commit()

    async def load_config(self) -> Optional[CrawlerConfig]:
        """Load crawl configuration from the database.

        Returns:
            The stored CrawlerConfig, or None if no config has been stored.
        """
        cursor = await self.db.execute("SELECT * FROM crawl_config WHERE id = 1")
        row = await cursor.fetchone()
        if row is None:
            return None

        return CrawlerConfig(
            seed_url=row["seed_url"],
            max_depth=row["max_depth"],
            max_concurrency=row["max_concurrency"],
            max_retries=row["max_retries"],
            max_content_size=row["max_content_size"],
            max_redirects=row["max_redirects"],
            lease_timeout_ms=row["lease_timeout_ms"],
            batch_size=row["batch_size"],
            progress_interval_ms=row["progress_interval_ms"],
            include_patterns=json.loads(row["include_patterns"] or "[]"),
            exclude_patterns=json.loads(row["exclude_patterns"] or "[]"),
        )

    async def load_seed_domain(self) -> Optional[str]:
        """Load the seed domain from the stored config.

        Returns:
            The seed domain string, or None if no config stored.
        """
        cursor = await self.db.execute(
            "SELECT seed_domain FROM crawl_config WHERE id = 1"
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return row["seed_domain"]

    # ------------------------------------------------------------------
    # Crawl frontier operations (Task 3.2 — stubs)
    # ------------------------------------------------------------------

    async def enqueue(
        self,
        normalized_url: str,
        url: str,
        depth: int,
        parent_url: Optional[str] = None,
        redirect_count: int = 0,
    ) -> None:
        """Atomically insert a URL into the frontier if not already present."""
        raise NotImplementedError("Implemented in task 3.2")

    async def acquire_lease_batch(
        self, batch_size: int, lease_ttl_ms: int, worker_id: str = ""
    ) -> list[LeaseResult]:
        """Atomically acquire a batch of URLs for processing."""
        raise NotImplementedError("Implemented in task 3.2")

    async def renew_lease(
        self, normalized_url: str, lease_token: str, extension_ms: int
    ) -> bool:
        """Extend a lease by one additional TTL."""
        raise NotImplementedError("Implemented in task 3.2")

    async def expire_leases(self) -> int:
        """Reset expired In_Progress URLs back to Pending."""
        raise NotImplementedError("Implemented in task 3.2")

    # ------------------------------------------------------------------
    # State transitions and queries (Task 3.3 — stubs)
    # ------------------------------------------------------------------

    async def mark_completed(
        self,
        normalized_url: str,
        lease_token: str,
        content_hash: Optional[str] = None,
        content_type: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> None:
        """Mark a URL as completed with lease token validation."""
        raise NotImplementedError("Implemented in task 3.3")

    async def mark_retry(
        self,
        normalized_url: str,
        lease_token: str,
        retry_count: int,
        next_retry_at: int,
        reason: str,
    ) -> None:
        """Mark a URL for retry with backoff scheduling."""
        raise NotImplementedError("Implemented in task 3.3")

    async def mark_terminal_failed(
        self, normalized_url: str, lease_token: str, reason: str
    ) -> None:
        """Mark a URL as terminally failed (no retry)."""
        raise NotImplementedError("Implemented in task 3.3")

    async def mark_failed(self, normalized_url: str) -> None:
        """Mark a URL as permanently failed (max retries exceeded)."""
        raise NotImplementedError("Implemented in task 3.3")

    async def get_content_hash(self, normalized_url: str) -> Optional[str]:
        """Get the stored content hash for a URL."""
        raise NotImplementedError("Implemented in task 3.3")

    async def get_urls_by_state(self, state: str) -> list[dict]:
        """Get all URL records matching a given crawl state."""
        raise NotImplementedError("Implemented in task 3.3")

    async def get_state_counts(self) -> dict[str, int]:
        """Get counts of URLs in each crawl state."""
        raise NotImplementedError("Implemented in task 3.3")

    async def get_child_urls(self, parent_normalized_url: str) -> list[dict]:
        """Get all URLs discovered from a given parent URL."""
        raise NotImplementedError("Implemented in task 3.3")

    async def exists(self, normalized_url: str) -> bool:
        """Check if a URL already exists in the store."""
        raise NotImplementedError("Implemented in task 3.3")

    async def get_redirect_count(self, normalized_url: str) -> int:
        """Get the redirect count for a URL."""
        raise NotImplementedError("Implemented in task 3.3")

    # ------------------------------------------------------------------
    # Content-type metadata (Task 3.3 — stubs)
    # ------------------------------------------------------------------

    async def store_html_metadata(
        self, normalized_url: str, meta: HtmlMetadata
    ) -> None:
        """Store HTML-specific metadata."""
        raise NotImplementedError("Implemented in task 3.3")

    async def store_image_metadata(
        self, normalized_url: str, meta: ImageMetadata
    ) -> None:
        """Store image-specific metadata."""
        raise NotImplementedError("Implemented in task 3.3")

    async def store_video_metadata(
        self, normalized_url: str, meta: VideoMetadata
    ) -> None:
        """Store video-specific metadata."""
        raise NotImplementedError("Implemented in task 3.3")

    async def store_pdf_metadata(self, normalized_url: str, meta: PdfMetadata) -> None:
        """Store PDF-specific metadata."""
        raise NotImplementedError("Implemented in task 3.3")


# ---------------------------------------------------------------------------
# Schema SQL
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
-- Crawl configuration (one row per crawl)
CREATE TABLE IF NOT EXISTS crawl_config (
    id INTEGER PRIMARY KEY DEFAULT 1,
    seed_url TEXT NOT NULL,
    seed_domain TEXT NOT NULL,
    max_depth INTEGER,
    max_concurrency INTEGER NOT NULL DEFAULT 5,
    max_retries INTEGER NOT NULL DEFAULT 3,
    max_content_size INTEGER NOT NULL DEFAULT 52428800,
    max_redirects INTEGER NOT NULL DEFAULT 5,
    lease_timeout_ms INTEGER NOT NULL DEFAULT 60000,
    batch_size INTEGER NOT NULL DEFAULT 50,
    progress_interval_ms INTEGER NOT NULL DEFAULT 10000,
    include_patterns TEXT,
    exclude_patterns TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Main URL records (crawl frontier + state)
CREATE TABLE IF NOT EXISTS url_records (
    normalized_url TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    crawl_state TEXT NOT NULL DEFAULT 'Pending'
        CHECK (crawl_state IN ('Pending','In_Progress','Completed','Retry','Failed','Terminal_Failed')),
    lease_owner_id TEXT,
    lease_token TEXT,
    lease_expires_at INTEGER,
    retry_count INTEGER NOT NULL DEFAULT 0,
    redirect_count INTEGER NOT NULL DEFAULT 0,
    next_retry_at INTEGER,
    crawl_depth INTEGER NOT NULL DEFAULT 0,
    parent_url TEXT,
    content_type TEXT,
    content_hash TEXT,
    etag TEXT,
    last_crawl_timestamp TEXT,
    failure_reason TEXT,
    discovered_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (parent_url) REFERENCES url_records(normalized_url)
);

-- Indexes for efficient frontier queries
CREATE INDEX IF NOT EXISTS idx_url_records_state
    ON url_records(crawl_state);
CREATE INDEX IF NOT EXISTS idx_url_records_retry
    ON url_records(crawl_state, next_retry_at)
    WHERE crawl_state = 'Retry';
CREATE INDEX IF NOT EXISTS idx_url_records_lease
    ON url_records(crawl_state, lease_expires_at)
    WHERE crawl_state = 'In_Progress';
CREATE INDEX IF NOT EXISTS idx_url_records_parent
    ON url_records(parent_url);

-- HTML-specific metadata
CREATE TABLE IF NOT EXISTS html_metadata (
    normalized_url TEXT PRIMARY KEY,
    page_title TEXT NOT NULL DEFAULT '',
    link_count INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (normalized_url) REFERENCES url_records(normalized_url)
);

-- Image-specific metadata
CREATE TABLE IF NOT EXISTS image_metadata (
    normalized_url TEXT PRIMARY KEY,
    width INTEGER,
    height INTEGER,
    file_size_bytes INTEGER NOT NULL,
    FOREIGN KEY (normalized_url) REFERENCES url_records(normalized_url)
);

-- Video-specific metadata
CREATE TABLE IF NOT EXISTS video_metadata (
    normalized_url TEXT PRIMARY KEY,
    file_size_bytes INTEGER NOT NULL,
    duration_seconds REAL,
    FOREIGN KEY (normalized_url) REFERENCES url_records(normalized_url)
);

-- PDF-specific metadata
CREATE TABLE IF NOT EXISTS pdf_metadata (
    normalized_url TEXT PRIMARY KEY,
    page_count INTEGER,
    document_title TEXT,
    FOREIGN KEY (normalized_url) REFERENCES url_records(normalized_url)
);
"""
