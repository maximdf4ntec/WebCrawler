"""
Property-based tests for MetadataStore lease operations.
Feature: web-crawler
Properties: 5 (Lease Mutual Exclusion), 6 (Expired Lease Recovery + Stale Write Rejection),
            7 (Lease Renewal Bound)
"""

import asyncio
import time

from hypothesis import given, settings, strategies as st
import pytest
import pytest_asyncio

from crawler.metadata_store import MetadataStore


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
# Helpers
# ---------------------------------------------------------------------------


async def _seed_urls(
    store: MetadataStore, count: int, domain: str = "example.com"
) -> list[str]:
    """Insert `count` Pending URLs and return their normalized_urls."""
    urls = []
    for i in range(count):
        url = f"https://{domain}/page{i}"
        await store.enqueue(url, url, depth=i % 5)
        urls.append(url)
    return urls


# ---------------------------------------------------------------------------
# Property 5: Lease Mutual Exclusion
# ---------------------------------------------------------------------------


class TestProperty5_LeaseMutualExclusion:
    """Feature: web-crawler, Property 5: Lease Mutual Exclusion

    For any URL u and any set of concurrent Workers, at most one Worker holds
    an active (non-expired) Lease on u at any given time.
    """

    @given(num_urls=st.integers(min_value=1, max_value=10))
    @settings(max_examples=50)
    def test_concurrent_acquires_never_double_lease(self, num_urls: int) -> None:
        """Multiple acquire_lease_batch calls never return the same URL twice."""

        async def _run() -> None:
            store = MetadataStore(db_path=":memory:")
            await store.init()
            try:
                await _seed_urls(store, num_urls)

                # Simulate concurrent workers each acquiring 1 URL
                all_leases = []
                for _ in range(num_urls + 3):  # more attempts than URLs
                    batch = await store.acquire_lease_batch(
                        batch_size=1, lease_ttl_ms=60000
                    )
                    all_leases.extend(batch)

                # Each URL should appear at most once
                leased_urls = [lease.normalized_url for lease in all_leases]
                assert len(leased_urls) == len(
                    set(leased_urls)
                ), f"Duplicate lease detected: {leased_urls}"
                # Should have leased exactly num_urls
                assert len(leased_urls) == num_urls
            finally:
                await store.close()

        asyncio.run(_run())

    @given(batch_size=st.integers(min_value=1, max_value=5))
    @settings(max_examples=30)
    def test_batch_acquire_produces_distinct_urls(self, batch_size: int) -> None:
        """A single batch never contains duplicate URLs."""

        async def _run() -> None:
            store = MetadataStore(db_path=":memory:")
            await store.init()
            try:
                await _seed_urls(store, batch_size * 2)

                batch = await store.acquire_lease_batch(
                    batch_size=batch_size, lease_ttl_ms=60000
                )
                urls_in_batch = [lease.normalized_url for lease in batch]
                assert len(urls_in_batch) == len(set(urls_in_batch))
            finally:
                await store.close()

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# Property 6: Expired Lease Recovery and Stale Write Rejection
# ---------------------------------------------------------------------------


class TestProperty6_ExpiredLeaseRecoveryAndStaleWrite:
    """Feature: web-crawler, Property 6: Expired Lease Recovery and Stale Write Rejection

    For any URL with an expired lease, it becomes acquirable by another Worker;
    any subsequent write using the original token is rejected.
    """

    @given(ttl_ms=st.integers(min_value=100, max_value=5000))
    @settings(max_examples=30)
    def test_expired_lease_becomes_reacquirable(self, ttl_ms: int) -> None:
        """After lease expires, URL can be acquired by another worker."""

        async def _run() -> None:
            store = MetadataStore(db_path=":memory:")
            await store.init()
            try:
                await store.enqueue(
                    "https://example.com/x", "https://example.com/x", depth=0
                )

                # Acquire with very short TTL
                leases = await store.acquire_lease_batch(
                    batch_size=1, lease_ttl_ms=ttl_ms
                )
                assert len(leases) == 1
                original_token = leases[0].lease_token

                # Manually expire the lease
                past_time = int(time.time() * 1000) - 1000
                await store.db.execute(
                    "UPDATE url_records SET lease_expires_at = ? WHERE normalized_url = ?",
                    (past_time, "https://example.com/x"),
                )
                await store.db.commit()

                # Should be re-acquirable
                new_leases = await store.acquire_lease_batch(
                    batch_size=1, lease_ttl_ms=60000
                )
                assert len(new_leases) == 1
                assert new_leases[0].lease_token != original_token
            finally:
                await store.close()

        asyncio.run(_run())

    @given(ttl_ms=st.integers(min_value=100, max_value=5000))
    @settings(max_examples=30)
    def test_stale_token_write_rejected_after_reacquire(self, ttl_ms: int) -> None:
        """After re-acquisition, a write with the original token is rejected."""

        async def _run() -> None:
            store = MetadataStore(db_path=":memory:")
            await store.init()
            try:
                await store.enqueue(
                    "https://example.com/x", "https://example.com/x", depth=0
                )

                # First acquire
                leases = await store.acquire_lease_batch(
                    batch_size=1, lease_ttl_ms=ttl_ms
                )
                stale_token = leases[0].lease_token

                # Expire and re-acquire
                past_time = int(time.time() * 1000) - 1000
                await store.db.execute(
                    "UPDATE url_records SET lease_expires_at = ? WHERE normalized_url = ?",
                    (past_time, "https://example.com/x"),
                )
                await store.db.commit()

                new_leases = await store.acquire_lease_batch(
                    batch_size=1, lease_ttl_ms=60000
                )
                assert len(new_leases) == 1

                # Attempt to mark_completed with the stale token — should be rejected
                # (mark_completed is task 3.3, but we test renew_lease rejection here)
                renew_result = await store.renew_lease(
                    "https://example.com/x", stale_token, extension_ms=30000
                )
                assert renew_result is False
            finally:
                await store.close()

        asyncio.run(_run())


# ---------------------------------------------------------------------------
# Property 7: Lease Renewal Bound
# ---------------------------------------------------------------------------


class TestProperty7_LeaseRenewalBound:
    """Feature: web-crawler, Property 7: Lease Renewal Bound

    For any URL in a single lease cycle, a Worker can successfully renew
    the Lease at most 3 consecutive times; the 4th renewal request SHALL fail.
    """

    @given(extension_ms=st.integers(min_value=1000, max_value=60000))
    @settings(max_examples=50)
    def test_exactly_three_renewals_then_failure(self, extension_ms: int) -> None:
        """Renewals 1-3 succeed; renewal 4 fails."""

        async def _run() -> None:
            store = MetadataStore(db_path=":memory:")
            await store.init()
            try:
                await store.enqueue(
                    "https://example.com/x", "https://example.com/x", depth=0
                )
                leases = await store.acquire_lease_batch(
                    batch_size=1, lease_ttl_ms=30000
                )
                lease = leases[0]

                # First 3 renewals succeed
                for i in range(3):
                    result = await store.renew_lease(
                        lease.normalized_url,
                        lease.lease_token,
                        extension_ms=extension_ms,
                    )
                    assert result is True, f"Renewal {i+1} should succeed"

                # 4th fails
                result = await store.renew_lease(
                    lease.normalized_url, lease.lease_token, extension_ms=extension_ms
                )
                assert result is False, "4th renewal should fail"
            finally:
                await store.close()

        asyncio.run(_run())

    @given(renewals_before_reacquire=st.integers(min_value=0, max_value=3))
    @settings(max_examples=20)
    def test_renewal_counter_resets_on_new_lease(
        self, renewals_before_reacquire: int
    ) -> None:
        """After a URL is released and re-acquired, the renewal counter resets."""

        async def _run() -> None:
            store = MetadataStore(db_path=":memory:")
            await store.init()
            try:
                await store.enqueue(
                    "https://example.com/x", "https://example.com/x", depth=0
                )

                # First lease cycle
                leases = await store.acquire_lease_batch(
                    batch_size=1, lease_ttl_ms=30000
                )
                lease = leases[0]
                for _ in range(renewals_before_reacquire):
                    await store.renew_lease(
                        lease.normalized_url, lease.lease_token, extension_ms=30000
                    )

                # Expire and re-acquire (simulate new lease cycle)
                past_time = int(time.time() * 1000) - 1000
                await store.db.execute(
                    "UPDATE url_records SET lease_expires_at = ? WHERE normalized_url = ?",
                    (past_time, "https://example.com/x"),
                )
                await store.db.commit()

                new_leases = await store.acquire_lease_batch(
                    batch_size=1, lease_ttl_ms=30000
                )
                assert len(new_leases) == 1
                new_lease = new_leases[0]

                # Should get a fresh 3 renewals
                for i in range(3):
                    result = await store.renew_lease(
                        new_lease.normalized_url,
                        new_lease.lease_token,
                        extension_ms=30000,
                    )
                    assert result is True, f"Renewal {i+1} of new cycle should succeed"
            finally:
                await store.close()

        asyncio.run(_run())
