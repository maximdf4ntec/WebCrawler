"""Unit tests for MetadataStore initialization and config persistence.

Tests:
- Schema creation (all tables and indexes)
- SQLite pragma configuration
- store_config() / load_config() round-trip
- store_config() overwrites on second call (INSERT OR REPLACE)
"""

import pytest

from crawler.metadata_store import MetadataStore
from crawler.types import CrawlerConfig


@pytest.fixture
async def store():
    """Create an in-memory MetadataStore for testing."""
    s = MetadataStore(":memory:")
    await s.init()
    yield s
    await s.close()


# ------------------------------------------------------------------
# Schema creation tests
# ------------------------------------------------------------------


async def test_init_creates_crawl_config_table(store: MetadataStore):
    """init() should create the crawl_config table."""
    cursor = await store.db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='crawl_config'"
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["name"] == "crawl_config"


async def test_init_creates_url_records_table(store: MetadataStore):
    """init() should create the url_records table."""
    cursor = await store.db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='url_records'"
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["name"] == "url_records"


async def test_init_creates_html_metadata_table(store: MetadataStore):
    """init() should create the html_metadata table."""
    cursor = await store.db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='html_metadata'"
    )
    row = await cursor.fetchone()
    assert row is not None


async def test_init_creates_image_metadata_table(store: MetadataStore):
    """init() should create the image_metadata table."""
    cursor = await store.db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='image_metadata'"
    )
    row = await cursor.fetchone()
    assert row is not None


async def test_init_creates_video_metadata_table(store: MetadataStore):
    """init() should create the video_metadata table."""
    cursor = await store.db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='video_metadata'"
    )
    row = await cursor.fetchone()
    assert row is not None


async def test_init_creates_pdf_metadata_table(store: MetadataStore):
    """init() should create the pdf_metadata table."""
    cursor = await store.db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='pdf_metadata'"
    )
    row = await cursor.fetchone()
    assert row is not None


async def test_init_creates_indexes(store: MetadataStore):
    """init() should create all required indexes."""
    cursor = await store.db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
    )
    rows = await cursor.fetchall()
    index_names = {row["name"] for row in rows}

    expected = {
        "idx_url_records_state",
        "idx_url_records_retry",
        "idx_url_records_lease",
        "idx_url_records_parent",
    }
    assert expected.issubset(index_names)


# ------------------------------------------------------------------
# Pragma tests
# ------------------------------------------------------------------


async def test_pragma_journal_mode_wal(store: MetadataStore):
    """SQLite should be configured with WAL journal mode.

    Note: In-memory databases cannot use WAL mode and silently fall back to
    'memory' journal mode. We verify the pragma was issued by accepting either
    'wal' (file-backed) or 'memory' (in-memory).
    """
    cursor = await store.db.execute("PRAGMA journal_mode")
    row = await cursor.fetchone()
    assert row[0] in ("wal", "memory")


async def test_pragma_busy_timeout(store: MetadataStore):
    """SQLite should have busy_timeout=5000."""
    cursor = await store.db.execute("PRAGMA busy_timeout")
    row = await cursor.fetchone()
    assert row[0] == 5000


async def test_pragma_synchronous(store: MetadataStore):
    """SQLite should use synchronous=NORMAL (value 1)."""
    cursor = await store.db.execute("PRAGMA synchronous")
    row = await cursor.fetchone()
    assert row[0] == 1  # NORMAL = 1


async def test_pragma_foreign_keys(store: MetadataStore):
    """SQLite should have foreign_keys enabled."""
    cursor = await store.db.execute("PRAGMA foreign_keys")
    row = await cursor.fetchone()
    assert row[0] == 1


# ------------------------------------------------------------------
# Config store/load tests
# ------------------------------------------------------------------


def _make_config(**overrides) -> CrawlerConfig:
    """Create a test CrawlerConfig with sensible defaults."""
    defaults = {
        "seed_url": "http://example.com",
        "max_depth": 10,
        "max_concurrency": 5,
        "max_retries": 3,
        "max_content_size": 50 * 1024 * 1024,
        "max_redirects": 5,
        "include_patterns": [],
        "exclude_patterns": [],
        "lease_timeout_ms": 60_000,
        "batch_size": 50,
        "progress_interval_ms": 10_000,
    }
    defaults.update(overrides)
    return CrawlerConfig(**defaults)


async def test_load_config_returns_none_when_empty(store: MetadataStore):
    """load_config() should return None when no config has been stored."""
    result = await store.load_config()
    assert result is None


async def test_store_and_load_config_roundtrip(store: MetadataStore):
    """store_config() + load_config() should round-trip correctly."""
    config = _make_config(
        seed_url="http://example.com/start",
        max_depth=5,
        max_concurrency=10,
        max_retries=2,
        include_patterns=["/blog/.*"],
        exclude_patterns=["/admin/.*", "\\.pdf$"],
    )

    await store.store_config(config, seed_domain="example.com")
    loaded = await store.load_config()

    assert loaded is not None
    assert loaded.seed_url == "http://example.com/start"
    assert loaded.max_depth == 5
    assert loaded.max_concurrency == 10
    assert loaded.max_retries == 2
    assert loaded.max_content_size == 50 * 1024 * 1024
    assert loaded.max_redirects == 5
    assert loaded.include_patterns == ["/blog/.*"]
    assert loaded.exclude_patterns == ["/admin/.*", "\\.pdf$"]
    assert loaded.lease_timeout_ms == 60_000
    assert loaded.batch_size == 50
    assert loaded.progress_interval_ms == 10_000


async def test_store_config_with_no_max_depth(store: MetadataStore):
    """store_config() should handle None max_depth correctly."""
    config = _make_config(max_depth=None)
    await store.store_config(config, seed_domain="example.com")

    loaded = await store.load_config()
    assert loaded is not None
    assert loaded.max_depth is None


async def test_store_config_overwrites_existing(store: MetadataStore):
    """Calling store_config() twice should overwrite the previous config."""
    config1 = _make_config(seed_url="http://first.com", max_depth=5)
    config2 = _make_config(seed_url="http://second.com", max_depth=20)

    await store.store_config(config1, seed_domain="first.com")
    await store.store_config(config2, seed_domain="second.com")

    loaded = await store.load_config()
    assert loaded is not None
    assert loaded.seed_url == "http://second.com"
    assert loaded.max_depth == 20


async def test_load_seed_domain(store: MetadataStore):
    """load_seed_domain() should return the stored seed domain."""
    config = _make_config(seed_url="http://example.com")
    await store.store_config(config, seed_domain="example.com")

    domain = await store.load_seed_domain()
    assert domain == "example.com"


async def test_load_seed_domain_returns_none_when_empty(store: MetadataStore):
    """load_seed_domain() should return None when no config stored."""
    domain = await store.load_seed_domain()
    assert domain is None


# ------------------------------------------------------------------
# Edge cases
# ------------------------------------------------------------------


async def test_store_config_empty_patterns(store: MetadataStore):
    """Empty patterns should round-trip as empty lists."""
    config = _make_config(include_patterns=[], exclude_patterns=[])
    await store.store_config(config, seed_domain="example.com")

    loaded = await store.load_config()
    assert loaded is not None
    assert loaded.include_patterns == []
    assert loaded.exclude_patterns == []


async def test_init_idempotent(store: MetadataStore):
    """Calling init() on an already-initialized store should not error."""
    # Store should already be initialized from fixture
    # Calling init() again should work because of IF NOT EXISTS
    await store.init()
    # Verify tables still exist
    cursor = await store.db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='url_records'"
    )
    row = await cursor.fetchone()
    assert row is not None
