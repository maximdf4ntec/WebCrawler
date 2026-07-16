"""
Property-based tests for URLNormalizer.
Feature: web-crawler
Properties: 1 (Idempotence), 2 (Deduplication)
"""

from hypothesis import given, settings, strategies as st, assume
import pytest

from crawler.url_normalizer import URLNormalizer


# --- Hypothesis strategies ---

# Strategy: well-formed HTTP(S) URLs that the normalizer should accept
_SCHEMES = st.sampled_from(["http", "https"])
_HOSTS = st.from_regex(r"[a-z][a-z0-9]{0,10}(\.[a-z]{2,4}){1,2}", fullmatch=True)
_PORTS = st.one_of(
    st.none(),
    st.sampled_from([80, 443, 8080, 8443, 3000, 9090]),
)
_PATH_SEGMENTS = st.from_regex(r"[a-z0-9._~-]{1,10}", fullmatch=True)
_PATHS = st.lists(_PATH_SEGMENTS, min_size=0, max_size=4).map(
    lambda segs: "/" + "/".join(segs) if segs else "/"
)
_QUERY_KEYS = st.from_regex(r"[a-z]{1,5}", fullmatch=True)
_QUERY_VALUES = st.from_regex(r"[a-z0-9]{0,8}", fullmatch=True)
_QUERY_PAIRS = st.lists(st.tuples(_QUERY_KEYS, _QUERY_VALUES), min_size=0, max_size=4)
_FRAGMENTS = st.one_of(st.none(), st.from_regex(r"[a-z0-9]{1,8}", fullmatch=True))


@st.composite
def valid_http_urls(draw: st.DrawFn) -> str:
    """Generate syntactically valid HTTP/HTTPS URLs."""
    scheme = draw(_SCHEMES)
    host = draw(_HOSTS)
    port = draw(_PORTS)
    path = draw(_PATHS)
    query_pairs = draw(_QUERY_PAIRS)
    fragment = draw(_FRAGMENTS)

    # Build URL
    netloc = host
    if port is not None:
        netloc = f"{host}:{port}"

    query = "&".join(f"{k}={v}" for k, v in query_pairs)
    url = f"{scheme}://{netloc}{path}"
    if query:
        url += f"?{query}"
    if fragment:
        url += f"#{fragment}"
    return url


@st.composite
def case_varied_urls(draw: st.DrawFn) -> tuple[str, str]:
    """Generate two URLs differing only in case of scheme/host (should normalize the same)."""
    scheme = draw(st.sampled_from(["http", "https"]))
    host = draw(st.from_regex(r"[a-z]{3,8}\.[a-z]{2,4}", fullmatch=True))
    path = draw(_PATHS)

    url1 = f"{scheme}://{host}{path}"
    # Vary case in scheme and host
    scheme_upper = scheme.upper()
    host_upper = host.upper()
    url2 = f"{scheme_upper}://{host_upper}{path}"
    return url1, url2


@st.composite
def port_equivalent_urls(draw: st.DrawFn) -> tuple[str, str]:
    """Generate two URLs that differ by explicit default port vs. no port."""
    scheme = draw(st.sampled_from(["http", "https"]))
    host = draw(st.from_regex(r"[a-z]{3,8}\.[a-z]{2,4}", fullmatch=True))
    path = draw(_PATHS)

    default_port = 80 if scheme == "http" else 443
    url_with_port = f"{scheme}://{host}:{default_port}{path}"
    url_without_port = f"{scheme}://{host}{path}"
    return url_with_port, url_without_port


@st.composite
def fragment_varied_urls(draw: st.DrawFn) -> tuple[str, str]:
    """Generate two URLs differing only by fragment (should normalize the same)."""
    scheme = draw(st.sampled_from(["http", "https"]))
    host = draw(st.from_regex(r"[a-z]{3,8}\.[a-z]{2,4}", fullmatch=True))
    path = draw(_PATHS)
    frag = draw(st.from_regex(r"[a-z]{1,8}", fullmatch=True))

    url_no_frag = f"{scheme}://{host}{path}"
    url_with_frag = f"{scheme}://{host}{path}#{frag}"
    return url_no_frag, url_with_frag


@st.composite
def query_reordered_urls(draw: st.DrawFn) -> tuple[str, str]:
    """Generate two URLs with same query params in different order."""
    scheme = draw(st.sampled_from(["http", "https"]))
    host = draw(st.from_regex(r"[a-z]{3,8}\.[a-z]{2,4}", fullmatch=True))
    path = draw(_PATHS)
    # Need at least 2 distinct keys for reordering to matter
    keys = draw(
        st.lists(
            st.from_regex(r"[a-z]{1,5}", fullmatch=True),
            min_size=2,
            max_size=5,
            unique=True,
        )
    )
    values = draw(
        st.lists(
            st.from_regex(r"[a-z0-9]{1,5}", fullmatch=True),
            min_size=len(keys),
            max_size=len(keys),
        )
    )
    pairs = list(zip(keys, values))
    # Reverse for a different order
    reversed_pairs = list(reversed(pairs))
    assume(pairs != reversed_pairs)

    query1 = "&".join(f"{k}={v}" for k, v in pairs)
    query2 = "&".join(f"{k}={v}" for k, v in reversed_pairs)
    url1 = f"{scheme}://{host}{path}?{query1}"
    url2 = f"{scheme}://{host}{path}?{query2}"
    return url1, url2


# --- Fixtures ---


@pytest.fixture
def normalizer() -> URLNormalizer:
    return URLNormalizer()


# --- Property 1: Idempotence ---


class TestProperty1_Idempotence:
    """Feature: web-crawler, Property 1: URL Normalization Idempotence"""

    @given(url=valid_http_urls())
    @settings(max_examples=200)
    def test_normalize_is_idempotent(self, url: str) -> None:
        """normalize(normalize(u)) == normalize(u) for all valid URLs."""
        normalizer = URLNormalizer()
        once = normalizer.normalize(url)
        if once is None:
            return  # skip unparseable
        twice = normalizer.normalize(once)
        assert twice == once, (
            f"Idempotence violated: normalize({url!r}) = {once!r}, "
            f"but normalize({once!r}) = {twice!r}"
        )

    @given(url=valid_http_urls())
    @settings(max_examples=100)
    def test_normalize_returns_string_or_none(self, url: str) -> None:
        """normalize() returns either a non-empty string or None."""
        normalizer = URLNormalizer()
        result = normalizer.normalize(url)
        assert result is None or (isinstance(result, str) and len(result) > 0)


# --- Property 2: Deduplication (normalization equivalence classes) ---


class TestProperty2_Deduplication:
    """Feature: web-crawler, Property 2: URL Deduplication"""

    @given(pair=case_varied_urls())
    @settings(max_examples=100)
    def test_case_differences_normalize_to_same(self, pair: tuple[str, str]) -> None:
        """URLs differing only in scheme/host case normalize identically."""
        normalizer = URLNormalizer()
        url1, url2 = pair
        n1 = normalizer.normalize(url1)
        n2 = normalizer.normalize(url2)
        assert n1 is not None and n2 is not None
        assert n1 == n2, f"Case dedup failed: {url1!r} → {n1!r}, {url2!r} → {n2!r}"

    @given(pair=port_equivalent_urls())
    @settings(max_examples=100)
    def test_default_port_removal_normalizes_same(self, pair: tuple[str, str]) -> None:
        """URLs with explicit default port and without normalize identically."""
        normalizer = URLNormalizer()
        url_with, url_without = pair
        n1 = normalizer.normalize(url_with)
        n2 = normalizer.normalize(url_without)
        assert n1 is not None and n2 is not None
        assert n1 == n2, (
            f"Port dedup failed: {url_with!r} → {n1!r}, " f"{url_without!r} → {n2!r}"
        )

    @given(pair=fragment_varied_urls())
    @settings(max_examples=100)
    def test_fragment_stripped_normalizes_same(self, pair: tuple[str, str]) -> None:
        """URLs differing only by fragment normalize identically."""
        normalizer = URLNormalizer()
        url_no_frag, url_with_frag = pair
        n1 = normalizer.normalize(url_no_frag)
        n2 = normalizer.normalize(url_with_frag)
        assert n1 is not None and n2 is not None
        assert n1 == n2, (
            f"Fragment dedup failed: {url_no_frag!r} → {n1!r}, "
            f"{url_with_frag!r} → {n2!r}"
        )

    @given(pair=query_reordered_urls())
    @settings(max_examples=100)
    def test_query_param_order_normalizes_same(self, pair: tuple[str, str]) -> None:
        """URLs with same query params in different order normalize identically."""
        normalizer = URLNormalizer()
        url1, url2 = pair
        n1 = normalizer.normalize(url1)
        n2 = normalizer.normalize(url2)
        assert n1 is not None and n2 is not None
        assert n1 == n2, (
            f"Query order dedup failed: {url1!r} → {n1!r}, " f"{url2!r} → {n2!r}"
        )
