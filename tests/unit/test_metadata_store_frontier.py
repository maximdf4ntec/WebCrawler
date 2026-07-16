"""
Unit tests for MetadataStore crawl frontier operations (Task 3.2).

Tests:
- enqueue() with INSERT OR IGNORE deduplication
- acquire_lease_batch() with priority ordering and atomic state transition
- renew_lease() with lease token validation
- expire_leases() resetting stale In_Progress URLs back to Pending

These tests use real SQLite in :memory: mode (no mocking of the DB layer).
"""

import time
import pytest
import pytest_asyncio

from crawler.metadata_store import MetadataStore
from crawler.types import CrawlState


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def store() -> MetadataStore:
    """Create an initialized in-memory MetadataStore."""
    s = MetadataStore(db_path=":memory:")
    await s.init()
    yield s
    await s.close()


# ---------------------------------------------------------------------------
# enqueue() — Atomic deduplication
# ---------------------------------------------------------------------------


class TestEnqueue:
    """MetadataStore.enqueue() inserts URLs atomically with deduplication."""

    @pytest.mark.asyncio
    async def test_enqueue_inserts_new_url_as_pending(
        self, store: MetadataStore
    ) -> None:
        """A freshly enqueued URL is in Pending state at the given depth."""
        await store.enqueue("https://example.com/", "https://example.com/", depth=0)

        cursor = await store.db.execute(
            "SELECT crawl_state, crawl_depth, url FROM url_records WHERE normalized_url = ?",
            ("https://example.com/",),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["crawl_state"] == "Pending"
        assert row["crawl_depth"] == 0
        assert row["url"] == "https://example.com/"

    @pytest.mark.asyncio
    async def test_enqueue_deduplicates_same_normalized_url(
        self, store: MetadataStore
    ) -> None:
        """Enqueueing the same normalized_url twice results in exactly one record."""
        await store.enqueue(
            "https://example.com/page", "https://example.com/page", depth=1
        )
        await store.enqueue(
            "https://example.com/page", "https://EXAMPLE.COM/page", depth=2
        )

        cursor = await store.db.execute(
            "SELECT COUNT(*) as cnt FROM url_records WHERE normalized_url = ?",
            ("https://example.com/page",),
        )
        row = await cursor.fetchone()
        assert row["cnt"] == 1

    @pytest.mark.asyncio
    async def test_enqueue_preserves_original_depth_on_duplicate(
        self, store: MetadataStore
    ) -> None:
        """Duplicate enqueue does not update the existing record's depth."""
        await store.enqueue("https://example.com/a", "https://example.com/a", depth=1)
        await store.enqueue("https://example.com/a", "https://example.com/a", depth=5)

        cursor = await store.db.execute(
            "SELECT crawl_depth FROM url_records WHERE normalized_url = ?",
            ("https://example.com/a",),
        )
        row = await cursor.fetchone()
        assert row["crawl_depth"] == 1

    @pytest.mark.asyncio
    async def test_enqueue_sets_parent_url(self, store: MetadataStore) -> None:
        """Parent URL is recorded when provided."""
        # Must enqueue parent first (foreign key constraint)
        await store.enqueue("https://example.com/", "https://example.com/", depth=0)
        await store.enqueue(
            "https://example.com/child",
            "https://example.com/child",
            depth=1,
            parent_url="https://example.com/",
        )

        cursor = await store.db.execute(
            "SELECT parent_url FROM url_records WHERE normalized_url = ?",
            ("https://example.com/child",),
        )
        row = await cursor.fetchone()
        assert row["parent_url"] == "https://example.com/"

    @pytest.mark.asyncio
    async def test_enqueue_sets_redirect_count(self, store: MetadataStore) -> None:
        """redirect_count is persisted on enqueue."""
        await store.enqueue(
            "https://example.com/redir",
            "https://example.com/redir",
            depth=0,
            redirect_count=2,
        )

        cursor = await store.db.execute(
            "SELECT redirect_count FROM url_records WHERE normalized_url = ?",
            ("https://example.com/redir",),
        )
        row = await cursor.fetchone()
        assert row["redirect_count"] == 2

    @pytest.mark.asyncio
    async def test_enqueue_multiple_distinct_urls(self, store: MetadataStore) -> None:
        """Multiple distinct URLs are all inserted."""
        urls = [f"https://example.com/page{i}" for i in range(5)]
        for i, url in enumerate(urls):
            await store.enqueue(url, url, depth=i)

        cursor = await store.db.execute("SELECT COUNT(*) as cnt FROM url_records")
        row = await cursor.fetchone()
        assert row["cnt"] == 5


# ---------------------------------------------------------------------------
# acquire_lease_batch() — Atomic lease acquisition
# ---------------------------------------------------------------------------


class TestAcquireLeaseBatch:
    """MetadataStore.acquire_lease_batch() atomically leases URLs for processing."""

    @pytest.mark.asyncio
    async def test_acquires_pending_urls(self, store: MetadataStore) -> None:
        """Pending URLs are leased and transition to In_Progress."""
        await store.enqueue("https://example.com/a", "https://example.com/a", depth=0)
        await store.enqueue("https://example.com/b", "https://example.com/b", depth=1)

        leases = await store.acquire_lease_batch(batch_size=2, lease_ttl_ms=60000)

        assert len(leases) == 2
        for lease in leases:
            assert lease.lease_token != ""
            assert lease.lease_expires_at > 0

        # Verify state transition in DB
        cursor = await store.db.execute(
            "SELECT crawl_state FROM url_records WHERE normalized_url = ?",
            ("https://example.com/a",),
        )
        row = await cursor.fetchone()
        assert row["crawl_state"] == "In_Progress"

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_pending(self, store: MetadataStore) -> None:
        """Returns empty list when no URLs are available for leasing."""
        leases = await store.acquire_lease_batch(batch_size=10, lease_ttl_ms=60000)
        assert leases == []

    @pytest.mark.asyncio
    async def test_respects_batch_size_limit(self, store: MetadataStore) -> None:
        """Never returns more URLs than batch_size."""
        for i in range(10):
            await store.enqueue(
                f"https://example.com/{i}", f"https://example.com/{i}", depth=0
            )

        leases = await store.acquire_lease_batch(batch_size=3, lease_ttl_ms=60000)
        assert len(leases) == 3

    @pytest.mark.asyncio
    async def test_does_not_double_lease_same_url(self, store: MetadataStore) -> None:
        """A URL already In_Progress (with valid lease) is not re-leased."""
        await store.enqueue("https://example.com/x", "https://example.com/x", depth=0)

        first = await store.acquire_lease_batch(batch_size=1, lease_ttl_ms=60000)
        assert len(first) == 1

        # Second acquire should find nothing (URL is in In_Progress with valid lease)
        second = await store.acquire_lease_batch(batch_size=1, lease_ttl_ms=60000)
        assert second == []

    @pytest.mark.asyncio
    async def test_priority_retry_before_pending(self, store: MetadataStore) -> None:
        """Retry URLs (with elapsed backoff) are leased before Pending URLs."""
        # Insert a Pending URL
        await store.enqueue(
            "https://example.com/pending", "https://example.com/pending", depth=0
        )

        # Insert a Retry URL with next_retry_at in the past
        await store.enqueue(
            "https://example.com/retry", "https://example.com/retry", depth=0
        )
        past_time = int(time.time() * 1000) - 10000
        await store.db.execute(
            """UPDATE url_records
               SET crawl_state = 'Retry', next_retry_at = ?, retry_count = 1
               WHERE normalized_url = ?""",
            (past_time, "https://example.com/retry"),
        )
        await store.db.commit()

        leases = await store.acquire_lease_batch(batch_size=1, lease_ttl_ms=60000)
        assert len(leases) == 1
        assert leases[0].normalized_url == "https://example.com/retry"

    @pytest.mark.asyncio
    async def test_priority_expired_lease_before_pending(
        self, store: MetadataStore
    ) -> None:
        """Expired In_Progress URLs are leased before fresh Pending URLs."""
        # Insert Pending URL
        await store.enqueue(
            "https://example.com/fresh", "https://example.com/fresh", depth=0
        )

        # Insert expired In_Progress URL
        await store.enqueue(
            "https://example.com/expired", "https://example.com/expired", depth=0
        )
        past_time = int(time.time() * 1000) - 10000
        await store.db.execute(
            """UPDATE url_records
               SET crawl_state = 'In_Progress',
                   lease_token = 'old-token',
                   lease_expires_at = ?
               WHERE normalized_url = ?""",
            (past_time, "https://example.com/expired"),
        )
        await store.db.commit()

        leases = await store.acquire_lease_batch(batch_size=1, lease_ttl_ms=60000)
        assert len(leases) == 1
        assert leases[0].normalized_url == "https://example.com/expired"

    @pytest.mark.asyncio
    async def test_bfs_ordering_shallow_first(self, store: MetadataStore) -> None:
        """Within the same priority tier, shallower URLs are leased first (BFS)."""
        await store.enqueue(
            "https://example.com/deep", "https://example.com/deep", depth=5
        )
        await store.enqueue(
            "https://example.com/shallow", "https://example.com/shallow", depth=1
        )
        await store.enqueue(
            "https://example.com/mid", "https://example.com/mid", depth=3
        )

        leases = await store.acquire_lease_batch(batch_size=3, lease_ttl_ms=60000)
        depths = [lease.depth for lease in leases]
        assert depths == sorted(depths)  # ascending order

    @pytest.mark.asyncio
    async def test_lease_token_is_unique_per_acquisition(
        self, store: MetadataStore
    ) -> None:
        """Each lease gets a unique token."""
        for i in range(5):
            await store.enqueue(
                f"https://example.com/{i}", f"https://example.com/{i}", depth=0
            )

        leases = await store.acquire_lease_batch(batch_size=5, lease_ttl_ms=60000)
        tokens = [lease.lease_token for lease in leases]
        assert len(set(tokens)) == len(tokens)  # all unique

    @pytest.mark.asyncio
    async def test_lease_expires_at_is_in_future(self, store: MetadataStore) -> None:
        """Lease expiration time is now + lease_ttl_ms."""
        await store.enqueue("https://example.com/x", "https://example.com/x", depth=0)
        now_before = int(time.time() * 1000)

        leases = await store.acquire_lease_batch(batch_size=1, lease_ttl_ms=30000)
        assert len(leases) == 1
        # Should be approximately now + 30000ms
        assert leases[0].lease_expires_at >= now_before + 30000
        assert leases[0].lease_expires_at <= now_before + 30000 + 1000  # tolerance

    @pytest.mark.asyncio
    async def test_retry_with_future_next_retry_at_not_leased(
        self, store: MetadataStore
    ) -> None:
        """Retry URLs whose next_retry_at is in the future are NOT leased."""
        await store.enqueue(
            "https://example.com/wait", "https://example.com/wait", depth=0
        )
        future_time = int(time.time() * 1000) + 60000
        await store.db.execute(
            """UPDATE url_records
               SET crawl_state = 'Retry', next_retry_at = ?, retry_count = 1
               WHERE normalized_url = ?""",
            (future_time, "https://example.com/wait"),
        )
        await store.db.commit()

        leases = await store.acquire_lease_batch(batch_size=10, lease_ttl_ms=60000)
        assert leases == []


# ---------------------------------------------------------------------------
# renew_lease() — Lease extension with bound
# ---------------------------------------------------------------------------


class TestRenewLease:
    """MetadataStore.renew_lease() extends a lease with validation."""

    @pytest.mark.asyncio
    async def test_renew_succeeds_with_valid_token(self, store: MetadataStore) -> None:
        """Renewal with the correct lease token succeeds."""
        await store.enqueue("https://example.com/x", "https://example.com/x", depth=0)
        leases = await store.acquire_lease_batch(batch_size=1, lease_ttl_ms=30000)
        lease = leases[0]

        result = await store.renew_lease(
            lease.normalized_url, lease.lease_token, extension_ms=30000
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_renew_fails_with_wrong_token(self, store: MetadataStore) -> None:
        """Renewal with an incorrect token is rejected."""
        await store.enqueue("https://example.com/x", "https://example.com/x", depth=0)
        leases = await store.acquire_lease_batch(batch_size=1, lease_ttl_ms=30000)

        result = await store.renew_lease(
            leases[0].normalized_url, "wrong-token", extension_ms=30000
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_renew_extends_lease_expiry(self, store: MetadataStore) -> None:
        """After renewal, lease_expires_at is extended by extension_ms."""
        await store.enqueue("https://example.com/x", "https://example.com/x", depth=0)
        leases = await store.acquire_lease_batch(batch_size=1, lease_ttl_ms=30000)
        lease = leases[0]
        original_expiry = lease.lease_expires_at

        await store.renew_lease(
            lease.normalized_url, lease.lease_token, extension_ms=30000
        )

        cursor = await store.db.execute(
            "SELECT lease_expires_at FROM url_records WHERE normalized_url = ?",
            (lease.normalized_url,),
        )
        row = await cursor.fetchone()
        assert row["lease_expires_at"] > original_expiry

    @pytest.mark.asyncio
    async def test_renew_fails_after_three_renewals(self, store: MetadataStore) -> None:
        """The 4th renewal attempt fails (max 3 per lease cycle, Property 7)."""
        await store.enqueue("https://example.com/x", "https://example.com/x", depth=0)
        leases = await store.acquire_lease_batch(batch_size=1, lease_ttl_ms=30000)
        lease = leases[0]

        # 3 successful renewals
        for _ in range(3):
            result = await store.renew_lease(
                lease.normalized_url, lease.lease_token, extension_ms=30000
            )
            assert result is True

        # 4th should fail
        result = await store.renew_lease(
            lease.normalized_url, lease.lease_token, extension_ms=30000
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_renew_fails_for_nonexistent_url(self, store: MetadataStore) -> None:
        """Renewal for a URL not in the store fails."""
        result = await store.renew_lease(
            "https://example.com/nonexistent", "some-token", extension_ms=30000
        )
        assert result is False


# ---------------------------------------------------------------------------
# expire_leases() — Reclaim expired leases
# ---------------------------------------------------------------------------


class TestExpireLeases:
    """MetadataStore.expire_leases() resets stale In_Progress URLs."""

    @pytest.mark.asyncio
    async def test_expires_past_due_leases(self, store: MetadataStore) -> None:
        """URLs with lease_expires_at in the past are reset to Pending."""
        await store.enqueue("https://example.com/x", "https://example.com/x", depth=0)

        # Manually set to In_Progress with expired lease
        past_time = int(time.time() * 1000) - 10000
        await store.db.execute(
            """UPDATE url_records
               SET crawl_state = 'In_Progress',
                   lease_token = 'old-token',
                   lease_owner_id = 'worker-1',
                   lease_expires_at = ?
               WHERE normalized_url = ?""",
            (past_time, "https://example.com/x"),
        )
        await store.db.commit()

        count = await store.expire_leases()
        assert count == 1

        cursor = await store.db.execute(
            "SELECT crawl_state, lease_token, lease_owner_id, lease_expires_at FROM url_records WHERE normalized_url = ?",
            ("https://example.com/x",),
        )
        row = await cursor.fetchone()
        assert row["crawl_state"] == "Pending"
        assert row["lease_token"] is None
        assert row["lease_owner_id"] is None
        assert row["lease_expires_at"] is None

    @pytest.mark.asyncio
    async def test_does_not_expire_valid_leases(self, store: MetadataStore) -> None:
        """URLs with lease_expires_at in the future are not expired."""
        await store.enqueue(
            "https://example.com/valid", "https://example.com/valid", depth=0
        )
        future_time = int(time.time() * 1000) + 60000
        await store.db.execute(
            """UPDATE url_records
               SET crawl_state = 'In_Progress',
                   lease_token = 'valid-token',
                   lease_expires_at = ?
               WHERE normalized_url = ?""",
            (future_time, "https://example.com/valid"),
        )
        await store.db.commit()

        count = await store.expire_leases()
        assert count == 0

        cursor = await store.db.execute(
            "SELECT crawl_state FROM url_records WHERE normalized_url = ?",
            ("https://example.com/valid",),
        )
        row = await cursor.fetchone()
        assert row["crawl_state"] == "In_Progress"

    @pytest.mark.asyncio
    async def test_returns_count_of_expired(self, store: MetadataStore) -> None:
        """Returns the number of leases expired."""
        past_time = int(time.time() * 1000) - 5000
        for i in range(3):
            url = f"https://example.com/expired{i}"
            await store.enqueue(url, url, depth=0)
            await store.db.execute(
                """UPDATE url_records
                   SET crawl_state = 'In_Progress',
                       lease_token = ?,
                       lease_expires_at = ?
                   WHERE normalized_url = ?""",
                (f"token-{i}", past_time, url),
            )
        await store.db.commit()

        count = await store.expire_leases()
        assert count == 3

    @pytest.mark.asyncio
    async def test_does_not_affect_completed_or_failed(
        self, store: MetadataStore
    ) -> None:
        """Only In_Progress URLs are candidates for expiration."""
        await store.enqueue(
            "https://example.com/done", "https://example.com/done", depth=0
        )
        await store.db.execute(
            """UPDATE url_records SET crawl_state = 'Completed' WHERE normalized_url = ?""",
            ("https://example.com/done",),
        )
        await store.db.commit()

        count = await store.expire_leases()
        assert count == 0
