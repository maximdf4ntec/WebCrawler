# Stub — implementation pending (Task 9.2)
from pathlib import Path

from crawler.types import CrawlResult


class Crawler:
    """Top-level orchestrator: validates config, bootstraps, runs scheduler."""

    async def start(self, config_path: Path) -> CrawlResult:
        """Load config from YAML, validate, freeze to DB, and begin crawling."""
        raise NotImplementedError

    async def resume(self) -> CrawlResult:
        """Resume using frozen config from DB (ignores YAML)."""
        raise NotImplementedError

    async def stop(self) -> None:
        """Graceful shutdown."""
        raise NotImplementedError
