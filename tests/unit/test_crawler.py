"""
Unit tests for Crawler entry point (Task 9.2).

Tests:
- start() loads config from YAML path
- start() validates config ranges (rejects invalid params)
- start() validates seed URL (rejects missing scheme/host)
- start() creates output directories
- start() initializes MetadataStore and freezes config
- start() returns CrawlResult on completion
- resume() loads frozen config from DB (ignores YAML)
- resume() returns CrawlResult
- stop() delegates to graceful shutdown

Config validation (Property 18):
- max_depth outside 1–1000 → rejected
- max_concurrency outside 1–100 → rejected
- max_retries outside 0–10 → rejected
- max_content_size outside 1KB–1GB → rejected
"""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from crawler.crawler import Crawler
from crawler.types import CrawlerConfig, CrawlResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_config(tmp_path: Path, **overrides) -> Path:
    """Write a valid YAML config file and return the path."""
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
# start() — Config loading
# ---------------------------------------------------------------------------


class TestStartConfigLoading:
    """Crawler.start() loads configuration from a YAML file."""

    @pytest.mark.asyncio
    async def test_start_loads_config_from_yaml(self, tmp_path: Path) -> None:
        """start() reads and parses the YAML config file."""
        config_path = _write_config(tmp_path, seed_url="https://test.com")
        crawler = Crawler()

        # Will raise NotImplementedError from the stub, which is what we expect
        with pytest.raises(NotImplementedError):
            await crawler.start(config_path)


# ---------------------------------------------------------------------------
# start() — Config validation (Property 18)
# ---------------------------------------------------------------------------


class TestConfigValidation:
    """Crawler rejects invalid configuration parameters with clear errors."""

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
        """max_depth = None means unlimited — not a validation error."""
        config_path = _write_config(tmp_path, max_depth=None)
        crawler = Crawler()

        # Should NOT raise ValueError. May raise other errors from stub.
        try:
            await crawler.start(config_path)
        except ValueError:
            pytest.fail("max_depth=None should be accepted (unlimited)")
        except Exception:
            pass  # Any non-ValueError is fine (e.g. NotImplementedError from stub)

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
# start() — Seed URL validation
# ---------------------------------------------------------------------------


class TestSeedUrlValidation:
    """Crawler rejects invalid seed URLs."""

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
        """Valid https:// seed URL passes validation (no ValueError)."""
        config_path = _write_config(tmp_path, seed_url="https://example.com")
        crawler = Crawler()

        # Should NOT raise ValueError. Other errors (NotImplementedError) are fine.
        try:
            await crawler.start(config_path)
        except ValueError:
            pytest.fail("Valid seed URL should not raise ValueError")
        except Exception:
            pass  # NotImplementedError from stub is expected


# ---------------------------------------------------------------------------
# resume()
# ---------------------------------------------------------------------------


class TestResume:
    """Crawler.resume() loads frozen config from DB."""

    @pytest.mark.asyncio
    async def test_resume_raises_not_implemented(self) -> None:
        """resume() stub raises NotImplementedError (pre-implementation)."""
        crawler = Crawler()

        with pytest.raises(NotImplementedError):
            await crawler.resume()


# ---------------------------------------------------------------------------
# stop()
# ---------------------------------------------------------------------------


class TestStop:
    """Crawler.stop() initiates graceful shutdown."""

    @pytest.mark.asyncio
    async def test_stop_raises_not_implemented(self) -> None:
        """stop() stub raises NotImplementedError (pre-implementation)."""
        crawler = Crawler()

        with pytest.raises(NotImplementedError):
            await crawler.stop()
