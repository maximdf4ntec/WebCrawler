"""
Unit tests for Crawler entry point (Task 9.2).

Tests:
- start() validates config ranges (rejects invalid params with ValueError)
- start() validates seed URL (rejects missing scheme/host with ValueError)
- Accepts valid configs without ValueError (other errors are acceptable)

Config validation (Property 18):
- max_depth outside 1–1000 → rejected
- max_concurrency outside 1–100 → rejected
- max_retries outside 0–10 → rejected
- max_content_size outside 1KB–1GB → rejected

NOTE: These tests validate the configuration validation layer only.
They do NOT run a full crawl. Tests that exercise the full crawl lifecycle
belong in tests/integration/.
"""

from pathlib import Path

import pytest
import yaml

from crawler.crawler import Crawler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_config(tmp_path: Path, **overrides) -> Path:
    """Write a YAML config file and return the path."""
    config = {
        "seed_url": "https://example.com",
        "max_depth": 10,
        "max_concurrency": 5,
        "max_retries": 3,
        "max_content_size": 1048576,  # 1MB
        "batch_size": 50,
        "lease_timeout_ms": 60000,
    }
    config.update(overrides)
    config_path = tmp_path / "config.yaml"
    with open(config_path, "w") as f:
        yaml.dump(config, f)
    return config_path


# ---------------------------------------------------------------------------
# Config validation — rejection cases (Property 18)
# ---------------------------------------------------------------------------


class TestConfigValidation:
    """Crawler rejects invalid configuration parameters with ValueError."""

    @pytest.mark.asyncio
    async def test_rejects_max_depth_below_range(self, tmp_path: Path) -> None:
        """max_depth = 0 (below range 1–1000) → rejected with ValueError."""
        config_path = _write_config(tmp_path, max_depth=0)
        crawler = Crawler()

        with pytest.raises(ValueError):
            await crawler.start(config_path)

    @pytest.mark.asyncio
    async def test_rejects_max_depth_above_range(self, tmp_path: Path) -> None:
        """max_depth = 1001 (above range 1–1000) → rejected with ValueError."""
        config_path = _write_config(tmp_path, max_depth=1001)
        crawler = Crawler()

        with pytest.raises(ValueError):
            await crawler.start(config_path)

    @pytest.mark.asyncio
    async def test_accepts_max_depth_none_unlimited(self, tmp_path: Path) -> None:
        """max_depth = None means unlimited — not a validation error.

        We only validate the config parsing step, not a full crawl.
        """
        from crawler.types import CrawlerConfig

        config_path = _write_config(tmp_path, max_depth=None)
        # Validate that config loads without ValueError
        config = CrawlerConfig.from_yaml(config_path)
        # The crawler's validation should accept max_depth=None
        assert config.max_depth is None

    @pytest.mark.asyncio
    async def test_rejects_max_concurrency_below_range(self, tmp_path: Path) -> None:
        """max_concurrency = 0 (below range 1–100) → rejected with ValueError."""
        config_path = _write_config(tmp_path, max_concurrency=0)
        crawler = Crawler()

        with pytest.raises(ValueError):
            await crawler.start(config_path)

    @pytest.mark.asyncio
    async def test_rejects_max_concurrency_above_range(self, tmp_path: Path) -> None:
        """max_concurrency = 101 (above range 1–100) → rejected with ValueError."""
        config_path = _write_config(tmp_path, max_concurrency=101)
        crawler = Crawler()

        with pytest.raises(ValueError):
            await crawler.start(config_path)

    @pytest.mark.asyncio
    async def test_rejects_max_retries_below_range(self, tmp_path: Path) -> None:
        """max_retries = -1 (below range 0–10) → rejected with ValueError."""
        config_path = _write_config(tmp_path, max_retries=-1)
        crawler = Crawler()

        with pytest.raises(ValueError):
            await crawler.start(config_path)

    @pytest.mark.asyncio
    async def test_rejects_max_retries_above_range(self, tmp_path: Path) -> None:
        """max_retries = 11 (above range 0–10) → rejected with ValueError."""
        config_path = _write_config(tmp_path, max_retries=11)
        crawler = Crawler()

        with pytest.raises(ValueError):
            await crawler.start(config_path)

    @pytest.mark.asyncio
    async def test_rejects_max_content_size_below_1kb(self, tmp_path: Path) -> None:
        """max_content_size < 1024 (below 1KB) → rejected with ValueError."""
        config_path = _write_config(tmp_path, max_content_size=512)
        crawler = Crawler()

        with pytest.raises(ValueError):
            await crawler.start(config_path)

    @pytest.mark.asyncio
    async def test_rejects_max_content_size_above_1gb(self, tmp_path: Path) -> None:
        """max_content_size > 1GB → rejected with ValueError."""
        config_path = _write_config(tmp_path, max_content_size=2 * 1024 * 1024 * 1024)
        crawler = Crawler()

        with pytest.raises(ValueError):
            await crawler.start(config_path)


# ---------------------------------------------------------------------------
# Seed URL validation
# ---------------------------------------------------------------------------


class TestSeedUrlValidation:
    """Crawler rejects invalid seed URLs with ValueError."""

    @pytest.mark.asyncio
    async def test_rejects_seed_url_without_scheme(self, tmp_path: Path) -> None:
        """Seed URL missing scheme → rejected with ValueError."""
        config_path = _write_config(tmp_path, seed_url="example.com/page")
        crawler = Crawler()

        with pytest.raises(ValueError):
            await crawler.start(config_path)

    @pytest.mark.asyncio
    async def test_rejects_seed_url_without_host(self, tmp_path: Path) -> None:
        """Seed URL missing host → rejected with ValueError."""
        config_path = _write_config(tmp_path, seed_url="http:///path")
        crawler = Crawler()

        with pytest.raises(ValueError):
            await crawler.start(config_path)

    @pytest.mark.asyncio
    async def test_accepts_valid_https_seed_url(self, tmp_path: Path) -> None:
        """Valid https:// seed URL passes validation (no ValueError).

        We only validate config parsing, not a full crawl.
        """
        from crawler.types import CrawlerConfig

        config_path = _write_config(tmp_path, seed_url="https://example.com")
        # Should load without ValueError
        config = CrawlerConfig.from_yaml(config_path)
        assert config.seed_url == "https://example.com"
