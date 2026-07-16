"""Crawler entry point — top-level orchestrator for the web crawl lifecycle.

Validates configuration, bootstraps infrastructure (output directories,
MetadataStore), freezes config to the database, and delegates to the
Scheduler for the actual crawl loop.

Requirements: 1.1, 1.2, 1.4, 1.5, 1.6, 15.1, 15.2, 19.1, 19.4
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from crawler.logger import get_logger
from crawler.metadata_store import MetadataStore
from crawler.scheduler import Scheduler
from crawler.types import CrawlerConfig, CrawlResult

logger = get_logger()

# ---------------------------------------------------------------------------
# Validation ranges (from design spec)
# ---------------------------------------------------------------------------
_MAX_DEPTH_MIN = 1
_MAX_DEPTH_MAX = 1000
_MAX_CONCURRENCY_MIN = 1
_MAX_CONCURRENCY_MAX = 100
_MAX_RETRIES_MIN = 0
_MAX_RETRIES_MAX = 10
_MAX_CONTENT_SIZE_MIN = 1024  # 1 KB
_MAX_CONTENT_SIZE_MAX = 1_073_741_824  # 1 GB
_BATCH_SIZE_MIN = 1
_BATCH_SIZE_MAX = 500

# Output subdirectories to create before crawl begins (Req 15.1)
_OUTPUT_SUBDIRS = ["html", "images", "videos", "pdfs"]


def _validate_config(config: CrawlerConfig) -> None:
    """Validate configuration parameter ranges.

    Raises:
        ValueError: If any parameter is outside its allowed range.
    """
    # max_depth: None (unlimited) OR 1–1000
    if config.max_depth is not None:
        if config.max_depth < _MAX_DEPTH_MIN or config.max_depth > _MAX_DEPTH_MAX:
            raise ValueError(
                f"max_depth must be None (unlimited) or between "
                f"{_MAX_DEPTH_MIN} and {_MAX_DEPTH_MAX}, got {config.max_depth}"
            )

    # max_concurrency: 1–100
    if (
        config.max_concurrency < _MAX_CONCURRENCY_MIN
        or config.max_concurrency > _MAX_CONCURRENCY_MAX
    ):
        raise ValueError(
            f"max_concurrency must be between {_MAX_CONCURRENCY_MIN} and "
            f"{_MAX_CONCURRENCY_MAX}, got {config.max_concurrency}"
        )

    # max_retries: 0–10
    if config.max_retries < _MAX_RETRIES_MIN or config.max_retries > _MAX_RETRIES_MAX:
        raise ValueError(
            f"max_retries must be between {_MAX_RETRIES_MIN} and "
            f"{_MAX_RETRIES_MAX}, got {config.max_retries}"
        )

    # max_content_size: 1 KB – 1 GB
    if (
        config.max_content_size < _MAX_CONTENT_SIZE_MIN
        or config.max_content_size > _MAX_CONTENT_SIZE_MAX
    ):
        raise ValueError(
            f"max_content_size must be between {_MAX_CONTENT_SIZE_MIN} and "
            f"{_MAX_CONTENT_SIZE_MAX}, got {config.max_content_size}"
        )

    # batch_size: 1–500
    if config.batch_size < _BATCH_SIZE_MIN or config.batch_size > _BATCH_SIZE_MAX:
        raise ValueError(
            f"batch_size must be between {_BATCH_SIZE_MIN} and "
            f"{_BATCH_SIZE_MAX}, got {config.batch_size}"
        )


def _validate_seed_url(url: str) -> str:
    """Validate the seed URL and return the extracted seed domain.

    Checks for missing scheme, missing host, and unparseable characters.

    Args:
        url: The seed URL to validate.

    Returns:
        The seed domain (host + port) extracted from the URL.

    Raises:
        ValueError: If the seed URL is malformed.
    """
    parsed = urlparse(url)

    # Reject missing scheme (Req 1.4)
    if not parsed.scheme:
        raise ValueError(
            f"Seed URL missing scheme (expected http:// or https://): {url}"
        )

    # Only allow http/https schemes
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Seed URL has unsupported scheme '{parsed.scheme}': {url}")

    # Reject missing host (Req 1.4)
    if not parsed.hostname:
        raise ValueError(f"Seed URL missing host: {url}")

    # Seed domain = netloc (host + optional port) (Req 1.2)
    return parsed.netloc


def _create_output_directories(base_path: Path) -> None:
    """Create output directories for storing crawled content.

    Creates: base_path/html/, base_path/images/, base_path/videos/, base_path/pdfs/
    Leaves existing directories intact (Req 15.1).

    Args:
        base_path: The base output directory path.

    Raises:
        OSError: If directories cannot be created (Req 15.2).
    """
    for subdir in _OUTPUT_SUBDIRS:
        dir_path = base_path / subdir
        try:
            dir_path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise OSError(
                f"Cannot create output directory '{dir_path}': {exc}"
            ) from exc


class Crawler:
    """Top-level orchestrator: validates config, bootstraps, runs scheduler.

    Usage:
        crawler = Crawler()
        result = await crawler.start(Path("config.yaml"))
        # ... or resume from a previous crawl:
        result = await crawler.resume()
        # Graceful shutdown from another coroutine:
        await crawler.stop()
    """

    def __init__(
        self,
        db_path: str = "crawl.db",
        output_path: str = "output",
    ) -> None:
        """Initialize the Crawler.

        Args:
            db_path: Path to the SQLite database file.
            output_path: Base path for output directories.
        """
        self._db_path = db_path
        self._output_path = Path(output_path)
        self._store: Optional[MetadataStore] = None
        self._scheduler: Optional[Scheduler] = None
        self._start_time_ms: int = 0

    async def start(self, config_path: Path) -> CrawlResult:
        """Load config from YAML, validate, freeze to DB, and begin crawling.

        Steps:
        1. Load config from YAML via CrawlerConfig.from_yaml()
        2. Validate configuration ranges (Req 19.1)
        3. Validate seed URL (Req 1.4)
        4. Extract seed domain (Req 1.2)
        5. Create output directories (Req 15.1)
        6. Initialize MetadataStore (Req 1.5)
        7. Freeze config to DB (Req 19.4)
        8. Create and initialize Scheduler
        9. Run the scheduler

        Args:
            config_path: Path to the YAML configuration file.

        Returns:
            CrawlResult with summary statistics.

        Raises:
            ValueError: If configuration or seed URL is invalid.
            RuntimeError: If MetadataStore cannot be initialized.
            OSError: If output directories cannot be created.
        """
        # 1. Load config (Req 19.1)
        config = CrawlerConfig.from_yaml(config_path)
        logger.info("config_loaded", config_path=str(config_path))

        # 2. Validate ranges
        _validate_config(config)

        # 3 & 4. Validate seed URL and extract domain (Req 1.1, 1.2, 1.4)
        seed_domain = _validate_seed_url(config.seed_url)
        logger.info(
            "seed_url_validated", seed_url=config.seed_url, seed_domain=seed_domain
        )

        # 5. Create output directories (Req 15.1, 15.2)
        try:
            _create_output_directories(self._output_path)
            logger.info(
                "output_directories_created", output_path=str(self._output_path)
            )
        except OSError as exc:
            logger.error(
                config.seed_url,
                error_type="output_directory_error",
                error_message=str(exc),
            )
            raise

        # 6. Initialize MetadataStore (Req 1.5)
        self._store = MetadataStore(db_path=self._db_path)
        try:
            await self._store.init()
        except Exception as exc:
            raise RuntimeError(
                f"MetadataStore unavailable at bootstrap: {exc}"
            ) from exc

        # 7. Freeze config to DB (Req 19.4)
        await self._store.store_config(config, seed_domain)
        logger.info("config_frozen_to_db", seed_domain=seed_domain)

        # 8 & 9. Create Scheduler, init, and run
        self._start_time_ms = int(time.time() * 1000)
        self._scheduler = Scheduler()
        await self._scheduler.init(config, self._store)
        await self._scheduler.run()

        # Compute result from state counts
        return await self._build_result()

    async def resume(self, db_path: Optional[str] = None) -> CrawlResult:
        """Resume a crawl using frozen config from the database.

        Loads the previously stored configuration from the MetadataStore
        and resumes crawling from the existing frontier state.

        Args:
            db_path: Optional override for the database path.

        Returns:
            CrawlResult with summary statistics.

        Raises:
            RuntimeError: If no stored config is found or DB is unavailable.
        """
        effective_db_path = db_path or self._db_path

        # Initialize MetadataStore
        self._store = MetadataStore(db_path=effective_db_path)
        try:
            await self._store.init()
        except Exception as exc:
            raise RuntimeError(f"MetadataStore unavailable for resume: {exc}") from exc

        # Load frozen config from DB
        config = await self._store.load_config()
        if config is None:
            await self._store.close()
            raise RuntimeError(
                "No stored configuration found in database. "
                "Cannot resume without a previous crawl session."
            )

        seed_domain = await self._store.load_seed_domain()
        logger.info(
            "resume_config_loaded",
            seed_url=config.seed_url,
            seed_domain=seed_domain,
        )

        # Create output directories (they may already exist)
        _create_output_directories(self._output_path)

        # Create Scheduler, init, and run
        self._start_time_ms = int(time.time() * 1000)
        self._scheduler = Scheduler()
        await self._scheduler.init(config, self._store)
        await self._scheduler.run()

        return await self._build_result()

    async def stop(self) -> None:
        """Graceful shutdown: stop scheduler and close MetadataStore."""
        logger.info("crawler_stop_requested")

        if self._scheduler is not None:
            await self._scheduler.shutdown()

        if self._store is not None:
            await self._store.close()

        logger.info("crawler_stopped")

    async def _build_result(self) -> CrawlResult:
        """Build a CrawlResult from MetadataStore state counts.

        Returns:
            CrawlResult with aggregate statistics.
        """
        duration_ms = int(time.time() * 1000) - self._start_time_ms

        if self._store is None:
            return CrawlResult(
                total_discovered=0,
                total_completed=0,
                total_failed=0,
                total_terminal_failed=0,
                duration_ms=duration_ms,
            )

        counts = await self._store.get_state_counts()

        total_discovered = sum(counts.values())
        total_completed = counts.get("Completed", 0)
        total_failed = counts.get("Failed", 0)
        total_terminal_failed = counts.get("Terminal_Failed", 0)

        return CrawlResult(
            total_discovered=total_discovered,
            total_completed=total_completed,
            total_failed=total_failed,
            total_terminal_failed=total_terminal_failed,
            duration_ms=duration_ms,
        )
