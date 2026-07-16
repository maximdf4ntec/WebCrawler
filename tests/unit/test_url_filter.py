"""
Unit tests for URLFilter.
Validates the filter chain from design.md section 7 (URL Filter).
"""

from unittest.mock import Mock

import pytest

from crawler.url_filter import URLFilter


# --- Helpers ---


def _mock_store(*, exists_return: bool = False) -> Mock:
    """Create a mock MetadataStore."""
    store = Mock()
    store.exists = Mock(return_value=exists_return)
    return store


def _make_filter(
    seed_domain: str = "example.com",
    max_depth: int | None = None,
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
    store_exists: bool = False,
) -> URLFilter:
    return URLFilter(
        seed_domain=seed_domain,
        max_depth=max_depth,
        include_patterns=include_patterns or [],
        exclude_patterns=exclude_patterns or [],
        store=_mock_store(exists_return=store_exists),
    )


# --- Filter Step 1: Scheme check (http/https only) ---


class TestSchemeFilter:
    """Filter rejects URLs with scheme != http or https."""

    def test_rejects_ftp_scheme(self) -> None:
        f = _make_filter()
        assert f.passes("ftp://example.com/file.txt", depth=0) is False

    def test_rejects_file_scheme(self) -> None:
        f = _make_filter()
        assert f.passes("file:///etc/passwd", depth=0) is False

    def test_rejects_mailto_scheme(self) -> None:
        f = _make_filter()
        assert f.passes("mailto:user@example.com", depth=0) is False

    def test_accepts_http_scheme(self) -> None:
        f = _make_filter()
        assert f.passes("http://example.com/page", depth=0) is True

    def test_accepts_https_scheme(self) -> None:
        f = _make_filter()
        assert f.passes("https://example.com/page", depth=0) is True


# --- Filter Step 2: Domain match ---


class TestDomainFilter:
    """Filter rejects URLs whose hostname != seed_domain."""

    def test_rejects_different_domain(self) -> None:
        f = _make_filter(seed_domain="example.com")
        assert f.passes("https://other.com/page", depth=0) is False

    def test_rejects_subdomain_mismatch(self) -> None:
        """A subdomain of seed_domain is NOT the same domain."""
        f = _make_filter(seed_domain="example.com")
        assert f.passes("https://sub.example.com/page", depth=0) is False

    def test_accepts_exact_domain_match(self) -> None:
        f = _make_filter(seed_domain="example.com")
        assert f.passes("https://example.com/page", depth=0) is True

    def test_accepts_www_when_seed_is_www(self) -> None:
        f = _make_filter(seed_domain="www.example.com")
        assert f.passes("https://www.example.com/page", depth=0) is True

    def test_rejects_bare_when_seed_is_www(self) -> None:
        """www.example.com != example.com."""
        f = _make_filter(seed_domain="www.example.com")
        assert f.passes("https://example.com/page", depth=0) is False


# --- Filter Step 3: Depth check ---


class TestDepthFilter:
    """Filter rejects URLs exceeding max_depth."""

    def test_rejects_depth_exceeding_max(self) -> None:
        f = _make_filter(max_depth=3)
        assert f.passes("https://example.com/deep/page", depth=4) is False

    def test_accepts_depth_equal_to_max(self) -> None:
        f = _make_filter(max_depth=3)
        assert f.passes("https://example.com/page", depth=3) is True

    def test_accepts_depth_below_max(self) -> None:
        f = _make_filter(max_depth=3)
        assert f.passes("https://example.com/page", depth=1) is True

    def test_no_depth_limit_accepts_any_depth(self) -> None:
        f = _make_filter(max_depth=None)
        assert f.passes("https://example.com/page", depth=999) is True

    def test_depth_zero_always_accepted(self) -> None:
        f = _make_filter(max_depth=1)
        assert f.passes("https://example.com/", depth=0) is True


# --- Filter Step 4: Exclude patterns ---


class TestExcludePatterns:
    """Filter rejects URLs matching any exclude pattern (re.search)."""

    def test_rejects_url_matching_exclude(self) -> None:
        f = _make_filter(exclude_patterns=[r"/admin"])
        assert f.passes("https://example.com/admin/dashboard", depth=0) is False

    def test_rejects_url_matching_any_exclude(self) -> None:
        f = _make_filter(exclude_patterns=[r"/admin", r"\.pdf$"])
        assert f.passes("https://example.com/docs/report.pdf", depth=0) is False

    def test_accepts_url_matching_no_exclude(self) -> None:
        f = _make_filter(exclude_patterns=[r"/admin", r"\.pdf$"])
        assert f.passes("https://example.com/page.html", depth=0) is True

    def test_exclude_uses_re_search_not_fullmatch(self) -> None:
        """re.search matches anywhere in the URL, not just from the start."""
        f = _make_filter(exclude_patterns=[r"secret"])
        assert f.passes("https://example.com/path/secret/page", depth=0) is False


# --- Filter Step 5: Include patterns ---


class TestIncludePatterns:
    """When include_patterns are set, reject URLs matching none of them."""

    def test_rejects_url_matching_no_include(self) -> None:
        f = _make_filter(include_patterns=[r"/blog"])
        assert f.passes("https://example.com/about", depth=0) is False

    def test_accepts_url_matching_include(self) -> None:
        f = _make_filter(include_patterns=[r"/blog"])
        assert f.passes("https://example.com/blog/post-1", depth=0) is True

    def test_accepts_url_matching_any_include(self) -> None:
        f = _make_filter(include_patterns=[r"/blog", r"/news"])
        assert f.passes("https://example.com/news/latest", depth=0) is True

    def test_no_include_patterns_means_all_pass(self) -> None:
        """Empty include_patterns list means no include filtering."""
        f = _make_filter(include_patterns=[])
        assert f.passes("https://example.com/anything", depth=0) is True

    def test_include_uses_re_search(self) -> None:
        """re.search matches anywhere in the URL."""
        f = _make_filter(include_patterns=[r"article"])
        assert f.passes("https://example.com/2024/article/42", depth=0) is True


# --- Filter Step 4+5: Exclude takes precedence over include ---


class TestExcludePrecedence:
    """Exclude patterns override include patterns per design.md."""

    def test_exclude_overrides_include(self) -> None:
        f = _make_filter(
            include_patterns=[r"/blog"],
            exclude_patterns=[r"/blog/draft"],
        )
        # Matches include (/blog) but also matches exclude (/blog/draft)
        assert f.passes("https://example.com/blog/draft/new-post", depth=0) is False

    def test_include_still_works_when_exclude_doesnt_match(self) -> None:
        f = _make_filter(
            include_patterns=[r"/blog"],
            exclude_patterns=[r"/admin"],
        )
        assert f.passes("https://example.com/blog/post", depth=0) is True


# --- Filter Step 6: Deduplication via MetadataStore.exists() ---


class TestDeduplication:
    """Filter rejects URLs that already exist in MetadataStore."""

    def test_rejects_already_seen_url(self) -> None:
        f = _make_filter(store_exists=True)
        assert f.passes("https://example.com/page", depth=0) is False

    def test_accepts_unseen_url(self) -> None:
        f = _make_filter(store_exists=False)
        assert f.passes("https://example.com/page", depth=0) is True


# --- Short-circuit behavior ---


class TestShortCircuit:
    """Filter short-circuits: earlier checks prevent later checks from running."""

    def test_bad_scheme_skips_domain_check(self) -> None:
        """Even if domain matches, bad scheme rejects."""
        f = _make_filter(seed_domain="example.com")
        assert f.passes("ftp://example.com/file", depth=0) is False

    def test_bad_domain_skips_pattern_checks(self) -> None:
        """Even if patterns would match, wrong domain rejects."""
        f = _make_filter(
            seed_domain="example.com",
            include_patterns=[r".*"],
        )
        assert f.passes("https://other.com/page", depth=0) is False
