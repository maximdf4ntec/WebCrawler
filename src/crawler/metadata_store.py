"""Metadata Store — async SQLite-backed persistence for crawl state.

Manages the crawl frontier, URL records, and content-type-specific metadata
tables. Uses aiosqlite for non-blocking database access with WAL mode for
concurrent read performance.

Requirements: 16.1, 16.2, 16.3, 2.5, 2.1, 2.2, 2.3, 2.4, 4.1, 4.2, 4.3, 4.4, 4.6, 4.7
"""

import json
import time
import uuid
from typing import Optional

import aiosqlite

from crawler.logger import get_logger
from crawler.types import (
    CrawlerConfig,
    HtmlMetadata,
    ImageMetadata,
    LeaseResult,
    PdfMetadata,
    VideoMetadata,
)

logger = get_logger()


# ---------------------------------------------------------------------------
# SQL Schema
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
  lease_timeout_ms INTEGER NOT NULL DEFAULT 60000,
  batch_size INTEGER NOT NULL DEFAULT 50,
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
  lease_renewal_count INTEGER NOT NULL DEFAULT 0,
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
CREATE INDEX IF NOT EXISTS idx_url_records_state ON url_records(crawl_state);
CREATE INDEX IF NOT EXISTS idx_url_records_retry ON url_records(crawl_state, next_retry_at)
  WHERE crawl_state = 'Retry';
CREATE INDEX IF NOT EXISTS idx_url_records_lease ON url_records(crawl_state, lease_expires_at)
  WHERE crawl_state = 'In_Progress';
CREATE INDEX IF NOT EXISTS idx_url_records_parent ON url_records(parent_url);

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


class MetadataStore:
    """Async SQLite-backed store for crawl metadata and frontier state.

    Manages schema creation, configuration persistence, and provides
    the foundation for frontier operations (added in later tasks).

    Attributes:
        db: The underlying aiosqlite connection (available after init()).
    """

    def __init__(self, db_path: str = "crawl.db") -> None:
        """Initialize the MetadataStore.

        Args:
            db_path: Path to the SQLite database file. Use ":memory:" for testing.
        """
        self._db_path = db_path
        self.db: Optional[aiosqlite.Connection] = None

    async def init(self) -> None:
        """Open the database connection, configure pragmas, and create schema.

        Configures SQLite with:
          - WAL journal mode for concurrent reads
          - busy_timeout=5000ms for lock contention handling
          - synchronous=NORMAL for a balance of safety and speed
          - foreign_keys=ON for referential integrity
        """
        self.db = await aiosqlite.connect(self._db_path)
        self.db.row_factory = aiosqlite.Row

        # Configure pragmas
        # WAL mode is not supported for in-memory databases; SQLite silently
        # falls back to "memory" journal mode. We still issue the pragma for
        # file-based databases where it takes effect.
        await self.db.execute("PRAGMA journal_mode = WAL")
        await self.db.execute("PRAGMA busy_timeout = 5000")
        await self.db.execute("PRAGMA synchronous = NORMAL")
        await self.db.execute("PRAGMA foreign_keys = ON")

        # Create schema
        await self.db.executescript(_SCHEMA_SQL)
        await self.db.commit()

        logger.info("metadata_store_initialized", db_path=self._db_path)

    async def store_config(self, config: CrawlerConfig, seed_domain: str) -> None:
        """Persist crawl configuration to the database.

        Uses INSERT OR REPLACE to allow overwriting an existing config row.
        List fields (include_patterns, exclude_patterns) are serialized as JSON.

        Args:
            config: The CrawlerConfig to persist.
            seed_domain: The extracted domain of the seed URL.
        """
        include_json = json.dumps(config.include_patterns)
        exclude_json = json.dumps(config.exclude_patterns)

        await self.db.execute(
            """
            INSERT OR REPLACE INTO crawl_config (
                id, seed_url, seed_domain, max_depth, max_concurrency,
                max_retries, max_content_size, lease_timeout_ms, batch_size,
                include_patterns, exclude_patterns
            ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                config.seed_url,
                seed_domain,
                config.max_depth,
                config.max_concurrency,
                config.max_retries,
                config.max_content_size,
                config.lease_timeout_ms,
                config.batch_size,
                include_json,
                exclude_json,
            ),
        )
        await self.db.commit()

        logger.info(
            "config_stored",
            seed_url=config.seed_url,
            seed_domain=seed_domain,
        )

    async def load_config(self) -> Optional[CrawlerConfig]:
        """Load the stored crawl configuration from the database.

        Deserializes JSON strings back to lists for pattern fields.

        Returns:
            The stored CrawlerConfig, or None if no config has been stored.
        """
        cursor = await self.db.execute("SELECT * FROM crawl_config WHERE id = 1")
        row = await cursor.fetchone()

        if row is None:
            return None

        include_patterns = (
            json.loads(row["include_patterns"]) if row["include_patterns"] else []
        )
        exclude_patterns = (
            json.loads(row["exclude_patterns"]) if row["exclude_patterns"] else []
        )

        return CrawlerConfig(
            seed_url=row["seed_url"],
            max_depth=row["max_depth"],
            max_concurrency=row["max_concurrency"],
            max_retries=row["max_retries"],
            max_content_size=row["max_content_size"],
            lease_timeout_ms=row["lease_timeout_ms"],
            batch_size=row["batch_size"],
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
        )

    async def load_seed_domain(self) -> Optional[str]:
        """Load the stored seed domain from the configuration.

        Returns:
            The seed domain string, or None if no config has been stored.
        """
        cursor = await self.db.execute(
            "SELECT seed_domain FROM crawl_config WHERE id = 1"
        )
        row = await cursor.fetchone()

        if row is None:
            return None

        return row["seed_domain"]

    async def close(self) -> None:
        """Close the database connection."""
        if self.db is not None:
            await self.db.close()
            self.db = None
            logger.info("metadata_store_closed")

    # ------------------------------------------------------------------
    # Crawl Frontier Operations (Task 3.2)
    # Requirements: 2.1, 2.2, 2.3, 2.4, 4.1, 4.2, 4.3, 4.4, 4.6, 4.7
    # ------------------------------------------------------------------

    async def enqueue(
        self,
        normalized_url: str,
        url: str,
        depth: int,
        parent_url: Optional[str] = None,
        redirect_count: int = 0,
    ) -> None:
        """Enqueue a URL for crawling with atomic deduplication.

        Uses INSERT OR IGNORE so that if the normalized_url already exists,
        the insert is silently skipped (no update to existing record).

        Args:
            normalized_url: The canonicalized URL (primary key).
            url: The original (raw) URL as discovered.
            depth: The crawl depth (hops from seed).
            parent_url: The normalized URL of the page that linked to this URL.
            redirect_count: Number of redirects in the chain leading here.
        """
        await self.db.execute(
            """
            INSERT OR IGNORE INTO url_records (
                normalized_url, url, crawl_depth, parent_url, redirect_count
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (normalized_url, url, depth, parent_url, redirect_count),
        )
        await self.db.commit()

    async def acquire_lease_batch(
        self, batch_size: int, lease_ttl_ms: int
    ) -> list[LeaseResult]:
        """Atomically acquire a batch of URLs for processing.

        Priority ordering (highest first):
          1. Retry URLs whose next_retry_at has elapsed
          2. In_Progress URLs whose lease has expired
          3. Pending URLs

        Within each priority tier, URLs are ordered by crawl_depth ASC (BFS).

        Each acquired URL transitions to In_Progress with a unique lease token
        and expiration timestamp.

        Args:
            batch_size: Maximum number of URLs to lease.
            lease_ttl_ms: Lease time-to-live in milliseconds.

        Returns:
            List of LeaseResult objects for the acquired URLs.
        """
        now_ms = int(time.time() * 1000)
        expires_at = now_ms + lease_ttl_ms

        # Select candidates using a priority UNION query:
        # Priority 1: Retry with elapsed backoff
        # Priority 2: In_Progress with expired lease
        # Priority 3: Pending
        cursor = await self.db.execute(
            """
            SELECT normalized_url, url, crawl_depth, 1 AS priority
            FROM url_records
            WHERE crawl_state = 'Retry' AND next_retry_at <= ?

            UNION ALL

            SELECT normalized_url, url, crawl_depth, 2 AS priority
            FROM url_records
            WHERE crawl_state = 'In_Progress' AND lease_expires_at <= ?

            UNION ALL

            SELECT normalized_url, url, crawl_depth, 3 AS priority
            FROM url_records
            WHERE crawl_state = 'Pending'

            ORDER BY priority ASC, crawl_depth ASC
            LIMIT ?
            """,
            (now_ms, now_ms, batch_size),
        )
        candidates = await cursor.fetchall()

        if not candidates:
            return []

        results: list[LeaseResult] = []
        for row in candidates:
            lease_token = uuid.uuid4().hex
            normalized = row["normalized_url"]

            # Atomic state transition: only update if the row is still in the
            # expected state (optimistic locking via WHERE conditions).
            cursor = await self.db.execute(
                """
                UPDATE url_records
                SET crawl_state = 'In_Progress',
                    lease_token = ?,
                    lease_expires_at = ?,
                    lease_renewal_count = 0
                WHERE normalized_url = ?
                  AND (
                    crawl_state = 'Pending'
                    OR (crawl_state = 'Retry' AND next_retry_at <= ?)
                    OR (crawl_state = 'In_Progress' AND lease_expires_at <= ?)
                  )
                """,
                (lease_token, expires_at, normalized, now_ms, now_ms),
            )

            # Check if the update actually took effect
            if cursor.rowcount > 0:
                results.append(
                    LeaseResult(
                        normalized_url=normalized,
                        url=row["url"],
                        depth=row["crawl_depth"],
                        lease_token=lease_token,
                        lease_expires_at=expires_at,
                    )
                )

        await self.db.commit()
        return results

    async def renew_lease(
        self, normalized_url: str, lease_token: str, extension_ms: int
    ) -> bool:
        """Extend a lease's expiration time.

        Validates that:
          - The URL exists and is In_Progress
          - The provided lease_token matches the current token
          - The renewal count has not reached the maximum (3)

        On success, increments the renewal count and extends lease_expires_at.

        Args:
            normalized_url: The URL whose lease to renew.
            lease_token: The token that must match the current lease holder.
            extension_ms: How many milliseconds to extend the lease by.

        Returns:
            True if the renewal succeeded, False if rejected.
        """
        now_ms = int(time.time() * 1000)
        new_expires_at = now_ms + extension_ms

        cursor = await self.db.execute(
            """
            UPDATE url_records
            SET lease_expires_at = ?,
                lease_renewal_count = lease_renewal_count + 1
            WHERE normalized_url = ?
              AND lease_token = ?
              AND crawl_state = 'In_Progress'
              AND lease_renewal_count < 3
            """,
            (new_expires_at, normalized_url, lease_token),
        )
        await self.db.commit()

        return cursor.rowcount > 0

    async def expire_leases(self) -> int:
        """Reset expired In_Progress URLs back to Pending.

        Any URL in In_Progress state whose lease_expires_at is in the past
        is reset to Pending with all lease fields cleared.

        Returns:
            The number of leases expired.
        """
        now_ms = int(time.time() * 1000)

        cursor = await self.db.execute(
            """
            UPDATE url_records
            SET crawl_state = 'Pending',
                lease_token = NULL,
                lease_owner_id = NULL,
                lease_expires_at = NULL,
                lease_renewal_count = 0
            WHERE crawl_state = 'In_Progress'
              AND lease_expires_at <= ?
            """,
            (now_ms,),
        )
        await self.db.commit()

        return cursor.rowcount

    # ------------------------------------------------------------------
    # State Transitions and Queries (Task 3.3)
    # Requirements: 16.4, 16.5, 16.6, 8.3, 14.2, 20.1, 20.2, 20.3, 20.4
    # ------------------------------------------------------------------

    async def mark_completed(
        self,
        normalized_url: str,
        lease_token: str,
        content_hash: Optional[str] = None,
        content_type: Optional[str] = None,
        etag: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> bool:
        """Mark a URL as successfully completed.

        Validates the lease token to reject stale writes. If the token does
        not match (lease was stolen/expired), the transition is rejected.

        Args:
            normalized_url: The URL to mark as completed.
            lease_token: The lease token that must match the current holder.
            content_hash: SHA-256 hash of the downloaded content.
            content_type: MIME type of the content.
            etag: ETag header value from the response.
            metadata: Optional dict of type-specific metadata (ignored here,
                      stored separately via store_*_metadata methods).

        Returns:
            True if the transition succeeded, False if rejected (stale write).
        """
        cursor = await self.db.execute(
            """
            UPDATE url_records
            SET crawl_state = 'Completed',
                content_hash = ?,
                content_type = ?,
                etag = ?,
                last_crawl_timestamp = datetime('now'),
                lease_token = NULL,
                lease_expires_at = NULL
            WHERE normalized_url = ?
              AND lease_token = ?
              AND crawl_state = 'In_Progress'
            """,
            (content_hash, content_type, etag, normalized_url, lease_token),
        )
        await self.db.commit()

        success = cursor.rowcount > 0
        if success:
            logger.state_transition(
                url=normalized_url,
                from_state="In_Progress",
                to_state="Completed",
            )
        return success

    async def mark_retry(
        self,
        normalized_url: str,
        lease_token: str,
        retry_count: int,
        next_retry_at: Optional[int] = None,
        next_retry_at_ms: Optional[int] = None,
        reason: Optional[str] = None,
    ) -> bool:
        """Mark a URL for retry with backoff scheduling.

        Validates the lease token to reject stale writes. Records the retry
        count and next eligible retry timestamp.

        Args:
            normalized_url: The URL to mark for retry.
            lease_token: The lease token that must match the current holder.
            retry_count: The updated retry attempt count.
            next_retry_at: Unix timestamp (ms) when this URL becomes eligible again.
            next_retry_at_ms: Alias for next_retry_at (either can be used).
            reason: Human-readable failure reason for the retry.

        Returns:
            True if the transition succeeded, False if rejected (stale write).
        """
        # Support both parameter names
        retry_at = next_retry_at if next_retry_at is not None else next_retry_at_ms

        cursor = await self.db.execute(
            """
            UPDATE url_records
            SET crawl_state = 'Retry',
                retry_count = ?,
                next_retry_at = ?,
                failure_reason = ?,
                lease_token = NULL,
                lease_expires_at = NULL
            WHERE normalized_url = ?
              AND lease_token = ?
              AND crawl_state = 'In_Progress'
            """,
            (retry_count, retry_at, reason, normalized_url, lease_token),
        )
        await self.db.commit()

        success = cursor.rowcount > 0
        if success:
            logger.state_transition(
                url=normalized_url,
                from_state="In_Progress",
                to_state="Retry",
                retry_count=retry_count,
                next_retry_at_ms=retry_at,
            )
        return success

    async def mark_terminal_failed(
        self,
        normalized_url: str,
        lease_token: str,
        failure_reason: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> bool:
        """Mark a URL as terminally failed (non-recoverable).

        Used for permanent errors (404, 403, unsupported content type, etc.)
        where retrying would not help.

        Args:
            normalized_url: The URL to mark as terminally failed.
            lease_token: The lease token that must match the current holder.
            failure_reason: Human-readable description of the failure.
            reason: Alias for failure_reason (either can be used).

        Returns:
            True if the transition succeeded, False if rejected (stale write).
        """
        the_reason = failure_reason if failure_reason is not None else reason

        cursor = await self.db.execute(
            """
            UPDATE url_records
            SET crawl_state = 'Terminal_Failed',
                failure_reason = ?,
                lease_token = NULL,
                lease_expires_at = NULL
            WHERE normalized_url = ?
              AND lease_token = ?
              AND crawl_state = 'In_Progress'
            """,
            (the_reason, normalized_url, lease_token),
        )
        await self.db.commit()

        success = cursor.rowcount > 0
        if success:
            logger.state_transition(
                url=normalized_url,
                from_state="In_Progress",
                to_state="Terminal_Failed",
                failure_reason=the_reason,
            )
        return success

    async def mark_failed(
        self,
        normalized_url: str,
        lease_token: Optional[str] = None,
        failure_reason: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> bool:
        """Mark a URL as failed (max retries exceeded).

        Can be called either:
          - With a lease_token: validates token and transitions from In_Progress → Failed
          - Without a lease_token: transitions directly from Retry → Failed (for scheduler use)

        Args:
            normalized_url: The URL to mark as failed.
            lease_token: The lease token (optional — if None, transitions from Retry).
            failure_reason: Human-readable description of the failure.
            reason: Alias for failure_reason (either can be used).

        Returns:
            True if the transition succeeded, False if rejected.
        """
        the_reason = failure_reason if failure_reason is not None else reason

        if lease_token is not None:
            # Lease-validated transition from In_Progress
            cursor = await self.db.execute(
                """
                UPDATE url_records
                SET crawl_state = 'Failed',
                    failure_reason = ?,
                    lease_token = NULL,
                    lease_expires_at = NULL
                WHERE normalized_url = ?
                  AND lease_token = ?
                  AND crawl_state = 'In_Progress'
                """,
                (the_reason, normalized_url, lease_token),
            )
        else:
            # Direct transition from Retry (scheduler marks exhausted retries)
            cursor = await self.db.execute(
                """
                UPDATE url_records
                SET crawl_state = 'Failed',
                    failure_reason = COALESCE(?, failure_reason),
                    lease_token = NULL,
                    lease_expires_at = NULL
                WHERE normalized_url = ?
                  AND crawl_state = 'Retry'
                """,
                (the_reason, normalized_url),
            )
        await self.db.commit()

        success = cursor.rowcount > 0
        if success:
            from_state = "In_Progress" if lease_token else "Retry"
            logger.state_transition(
                url=normalized_url,
                from_state=from_state,
                to_state="Failed",
                failure_reason=the_reason,
            )
        return success

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    async def exists(self, normalized_url: str) -> bool:
        """Check if a URL record exists in the store.

        Args:
            normalized_url: The URL to check.

        Returns:
            True if the URL exists, False otherwise.
        """
        cursor = await self.db.execute(
            "SELECT 1 FROM url_records WHERE normalized_url = ?",
            (normalized_url,),
        )
        row = await cursor.fetchone()
        return row is not None

    async def get_content_hash(self, normalized_url: str) -> Optional[str]:
        """Retrieve the content hash for a URL.

        Args:
            normalized_url: The URL to look up.

        Returns:
            The content_hash string, or None if the URL doesn't exist
            or has no hash stored.
        """
        cursor = await self.db.execute(
            "SELECT content_hash FROM url_records WHERE normalized_url = ?",
            (normalized_url,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return row["content_hash"]

    async def get_urls_by_state(self, state: str, limit: int = 500) -> list[dict]:
        """Retrieve URL records filtered by crawl state.

        Args:
            state: The crawl state to filter by (e.g. 'Pending', 'Completed').
            limit: Maximum number of records to return (1–500).

        Returns:
            List of URL record dictionaries.
        """
        # Clamp limit to valid range per Req 16.6
        limit = max(1, min(limit, 500))

        cursor = await self.db.execute(
            "SELECT * FROM url_records WHERE crawl_state = ? LIMIT ?",
            (state, limit),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_state_counts(self) -> dict[str, int]:
        """Get the count of URLs in each crawl state.

        Returns:
            Dictionary mapping state name to count.
        """
        cursor = await self.db.execute(
            "SELECT crawl_state, COUNT(*) as cnt FROM url_records GROUP BY crawl_state"
        )
        rows = await cursor.fetchall()
        return {row["crawl_state"]: row["cnt"] for row in rows}

    async def get_child_urls(self, parent_url: str) -> list[dict]:
        """Get all URLs discovered from a given parent URL.

        Args:
            parent_url: The normalized parent URL to query children of.

        Returns:
            List of URL record dicts that have this parent.
        """
        cursor = await self.db.execute(
            "SELECT * FROM url_records WHERE parent_url = ?",
            (parent_url,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_redirect_count(self, normalized_url: str) -> int:
        """Get the redirect count for a URL.

        Args:
            normalized_url: The URL to look up.

        Returns:
            The redirect_count value, or 0 if the URL doesn't exist.
        """
        cursor = await self.db.execute(
            "SELECT redirect_count FROM url_records WHERE normalized_url = ?",
            (normalized_url,),
        )
        row = await cursor.fetchone()
        if row is None:
            return 0
        return row["redirect_count"]

    async def get_retry_count(self, normalized_url: str) -> int:
        """Get the current retry count for a URL.

        Args:
            normalized_url: The URL to look up.

        Returns:
            The retry_count value, or 0 if the URL doesn't exist.
        """
        cursor = await self.db.execute(
            "SELECT retry_count FROM url_records WHERE normalized_url = ?",
            (normalized_url,),
        )
        row = await cursor.fetchone()
        if row is None:
            return 0
        return row["retry_count"]

    # ------------------------------------------------------------------
    # Type-specific metadata storage
    # ------------------------------------------------------------------

    async def store_html_metadata(
        self,
        normalized_url: str,
        metadata: Optional["HtmlMetadata"] = None,
        page_title: Optional[str] = None,
        link_count: Optional[int] = None,
    ) -> None:
        """Store HTML-specific metadata for a URL.

        Accepts either an HtmlMetadata model or individual field values.

        Args:
            normalized_url: The URL this metadata belongs to.
            metadata: An HtmlMetadata model (if provided, overrides individual args).
            page_title: The title of the HTML page.
            link_count: Number of links found on the page.
        """
        if metadata is not None:
            page_title = metadata.page_title
            link_count = metadata.link_count

        await self.db.execute(
            """
            INSERT OR REPLACE INTO html_metadata (normalized_url, page_title, link_count)
            VALUES (?, ?, ?)
            """,
            (normalized_url, page_title or "", link_count or 0),
        )
        await self.db.commit()

    async def store_image_metadata(
        self,
        normalized_url: str,
        metadata: Optional["ImageMetadata"] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        file_size_bytes: Optional[int] = None,
    ) -> None:
        """Store image-specific metadata for a URL.

        Accepts either an ImageMetadata model or individual field values.

        Args:
            normalized_url: The URL this metadata belongs to.
            metadata: An ImageMetadata model (if provided, overrides individual args).
            width: Image width in pixels (nullable).
            height: Image height in pixels (nullable).
            file_size_bytes: Size of the image file in bytes.
        """
        if metadata is not None:
            width = metadata.width
            height = metadata.height
            file_size_bytes = metadata.file_size_bytes

        await self.db.execute(
            """
            INSERT OR REPLACE INTO image_metadata (normalized_url, width, height, file_size_bytes)
            VALUES (?, ?, ?, ?)
            """,
            (normalized_url, width, height, file_size_bytes),
        )
        await self.db.commit()

    async def store_video_metadata(
        self,
        normalized_url: str,
        metadata: Optional["VideoMetadata"] = None,
        file_size_bytes: Optional[int] = None,
        duration_seconds: Optional[float] = None,
    ) -> None:
        """Store video-specific metadata for a URL.

        Accepts either a VideoMetadata model or individual field values.

        Args:
            normalized_url: The URL this metadata belongs to.
            metadata: A VideoMetadata model (if provided, overrides individual args).
            file_size_bytes: Size of the video file in bytes.
            duration_seconds: Duration of the video in seconds (nullable).
        """
        if metadata is not None:
            file_size_bytes = metadata.file_size_bytes
            duration_seconds = metadata.duration_seconds

        await self.db.execute(
            """
            INSERT OR REPLACE INTO video_metadata (normalized_url, file_size_bytes, duration_seconds)
            VALUES (?, ?, ?)
            """,
            (normalized_url, file_size_bytes, duration_seconds),
        )
        await self.db.commit()

    async def store_pdf_metadata(
        self,
        normalized_url: str,
        metadata: Optional["PdfMetadata"] = None,
        page_count: Optional[int] = None,
        document_title: Optional[str] = None,
    ) -> None:
        """Store PDF-specific metadata for a URL.

        Accepts either a PdfMetadata model or individual field values.

        Args:
            normalized_url: The URL this metadata belongs to.
            metadata: A PdfMetadata model (if provided, overrides individual args).
            page_count: Number of pages in the PDF (nullable).
            document_title: Title of the PDF document (nullable).
        """
        if metadata is not None:
            page_count = metadata.page_count
            document_title = metadata.document_title

        await self.db.execute(
            """
            INSERT OR REPLACE INTO pdf_metadata (normalized_url, page_count, document_title)
            VALUES (?, ?, ?)
            """,
            (normalized_url, page_count, document_title),
        )
        await self.db.commit()
