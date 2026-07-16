"""
Property-based tests for URLFilter.
Feature: web-crawler
Properties: 3 (Domain/Scheme/Depth Enforcement), 4 (Exclude Pattern Precedence)
"""

import asyncio
from unittest.mock import AsyncMock, Mock

from hypothesis import given, settings, assume, strategies as st
import pytest

from crawler.url_filter import URLFilter


# --- Helpers ---


def _mock_store(*, exists_return: bool = False) -> Mock:
    """Create a mock MetadataStore that returns a fixed value for exists()."""
    store = Mock()
    store.exists = AsyncMock(return_value=exists_return)
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


def _passes_sync(url_filter: URLFilter, url: str, depth: int) -> bool:
    """Run async passes() synchronously for hypothesis compatibility."""
    return asyncio.run(url_filter.passes(url, depth))


# --- Strategies ---

_VALID_SCHEMES = st.sampled_from(["http", "https"])
_INVALID_SCHEMES = st.sampled_from(
    ["ftp", "file", "mailto", "ssh", "ws", "wss", "data"]
)
_SUBDOMAINS = st.from_regex(r"[a-z]{2,6}", fullmatch=True)
_TLDS = st.sampled_from([".com", ".org", ".net", ".io"])
_PATHS = st.from_regex(r"(/[a-z0-9-]{1,10}){0,4}", fullmatch=True)


@st.composite
def same_domain_urls(draw: st.DrawFn, domain: str = "example.com") -> str:
    """Generate URLs on the given domain."""
    scheme = draw(_VALID_SCHEMES)
    path = draw(_PATHS)
    return f"{scheme}://{domain}{path or '/'}"


@st.composite
def different_domain_urls(draw: st.DrawFn, seed_domain: str = "example.com") -> str:
    """Generate URLs on a domain that is NOT the seed domain."""
    scheme = draw(_VALID_SCHEMES)
    sub = draw(_SUBDOMAINS)
    tld = draw(_TLDS)
    domain = f"{sub}{tld}"
    assume(domain != seed_domain)
    path = draw(_PATHS)
    return f"{scheme}://{domain}{path or '/'}"


@st.composite
def invalid_scheme_urls(draw: st.DrawFn) -> str:
    """Generate URLs with non-http(s) schemes on a valid domain."""
    scheme = draw(_INVALID_SCHEMES)
    path = draw(_PATHS)
    return f"{scheme}://example.com{path or '/'}"


# --- Property 3: Domain/Scheme/Depth Enforcement ---


class TestProperty3_DomainSchemeDepthEnforcement:
    """Feature: web-crawler, Property 3: URL Filter Domain/Scheme/Depth Enforcement"""

    @given(url=invalid_scheme_urls())
    @settings(max_examples=50)
    def test_rejects_non_http_https_scheme(self, url: str) -> None:
        """Any URL with scheme != http|https is rejected regardless of other criteria."""
        url_filter = _make_filter(seed_domain="example.com", max_depth=None)
        assert _passes_sync(url_filter, url, depth=0) is False

    @given(url=different_domain_urls(seed_domain="example.com"))
    @settings(max_examples=50)
    def test_rejects_different_domain(self, url: str) -> None:
        """Any URL whose domain != seed_domain is rejected."""
        url_filter = _make_filter(seed_domain="example.com", max_depth=None)
        assert _passes_sync(url_filter, url, depth=0) is False

    @given(
        url=same_domain_urls(domain="example.com"),
        max_depth=st.integers(min_value=1, max_value=50),
    )
    @settings(max_examples=50)
    def test_rejects_depth_exceeding_max(self, url: str, max_depth: int) -> None:
        """URL at depth > max_depth is rejected."""
        url_filter = _make_filter(seed_domain="example.com", max_depth=max_depth)
        over_depth = max_depth + 1
        assert _passes_sync(url_filter, url, depth=over_depth) is False

    @given(
        url=same_domain_urls(domain="example.com"),
        max_depth=st.integers(min_value=1, max_value=50),
        depth=st.integers(min_value=0, max_value=50),
    )
    @settings(max_examples=50)
    def test_accepts_depth_within_max(
        self, url: str, max_depth: int, depth: int
    ) -> None:
        """URL at depth <= max_depth passes the depth check (other checks may still reject)."""
        assume(depth <= max_depth)
        url_filter = _make_filter(seed_domain="example.com", max_depth=max_depth)
        # If depth is within range and no other filter rejects, it should pass
        result = _passes_sync(url_filter, url, depth=depth)
        # We can only assert it wasn't rejected for depth reasons,
        # but since seed_domain matches, scheme is valid, no patterns, no dedup → should pass
        assert result is True

    @given(url=same_domain_urls(domain="example.com"))
    @settings(max_examples=25)
    def test_unlimited_depth_never_rejects_for_depth(self, url: str) -> None:
        """When max_depth is None, no depth-based rejection occurs."""
        url_filter = _make_filter(seed_domain="example.com", max_depth=None)
        # Even at very high depth, it passes
        assert _passes_sync(url_filter, url, depth=9999) is True


# --- Property 4: Exclude Pattern Precedence ---


class TestProperty4_ExcludePatternPrecedence:
    """Feature: web-crawler, Property 4: Exclude Pattern Precedence"""

    @given(
        path_segment=st.from_regex(r"[a-z]{3,8}", fullmatch=True),
    )
    @settings(max_examples=50)
    def test_exclude_overrides_include(self, path_segment: str) -> None:
        """A URL matching both an exclude and an include pattern is rejected."""
        url = f"https://example.com/{path_segment}"
        # Pattern that matches the path_segment
        pattern = path_segment
        url_filter = _make_filter(
            seed_domain="example.com",
            max_depth=None,
            include_patterns=[pattern],
            exclude_patterns=[pattern],
        )
        assert _passes_sync(url_filter, url, depth=0) is False

    @given(
        path_segment=st.from_regex(r"[a-z]{3,8}", fullmatch=True),
    )
    @settings(max_examples=50)
    def test_exclude_rejects_matching_url(self, path_segment: str) -> None:
        """A URL matching an exclude pattern is rejected even with no include patterns."""
        url = f"https://example.com/{path_segment}"
        url_filter = _make_filter(
            seed_domain="example.com",
            max_depth=None,
            include_patterns=[],
            exclude_patterns=[path_segment],
        )
        assert _passes_sync(url_filter, url, depth=0) is False

    @given(
        path_segment=st.from_regex(r"[a-z]{3,8}", fullmatch=True),
        other_segment=st.from_regex(r"[a-z]{3,8}", fullmatch=True),
    )
    @settings(max_examples=50)
    def test_include_rejects_non_matching_url(
        self, path_segment: str, other_segment: str
    ) -> None:
        """When include_patterns are set, a URL matching none of them is rejected."""
        assume(path_segment != other_segment)
        # URL uses path_segment, but include pattern only allows other_segment
        url = f"https://example.com/{path_segment}"
        url_filter = _make_filter(
            seed_domain="example.com",
            max_depth=None,
            include_patterns=[f"^https://example\\.com/{other_segment}$"],
            exclude_patterns=[],
        )
        assert _passes_sync(url_filter, url, depth=0) is False
