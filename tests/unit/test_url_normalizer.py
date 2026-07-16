"""
Unit tests for URLNormalizer.
Validates each normalization step from design.md section 6 (URL Normalizer).
"""

import pytest

from crawler.url_normalizer import URLNormalizer


@pytest.fixture
def normalizer() -> URLNormalizer:
    return URLNormalizer()


# --- Step 1: Reject unparseable URLs (return None) ---


class TestRejectUnparseable:
    """normalize() returns None for URLs missing scheme or hostname."""

    def test_returns_none_for_no_scheme(self, normalizer: URLNormalizer) -> None:
        assert normalizer.normalize("example.com/path") is None

    def test_returns_none_for_empty_string(self, normalizer: URLNormalizer) -> None:
        assert normalizer.normalize("") is None

    def test_returns_none_for_no_host(self, normalizer: URLNormalizer) -> None:
        assert normalizer.normalize("http:///path") is None

    def test_returns_none_for_relative_path(self, normalizer: URLNormalizer) -> None:
        assert normalizer.normalize("/relative/path") is None

    def test_returns_none_for_just_fragment(self, normalizer: URLNormalizer) -> None:
        assert normalizer.normalize("#section") is None


# --- Step 2: Lowercase scheme and host ---


class TestLowercaseSchemeAndHost:
    """normalize() lowercases scheme and hostname."""

    def test_uppercase_scheme_lowercased(self, normalizer: URLNormalizer) -> None:
        result = normalizer.normalize("HTTP://example.com/path")
        assert result is not None
        assert result.startswith("http://")

    def test_mixed_case_host_lowercased(self, normalizer: URLNormalizer) -> None:
        result = normalizer.normalize("https://ExAmPlE.COM/path")
        assert result is not None
        assert "example.com" in result

    def test_scheme_and_host_both_lowercased(self, normalizer: URLNormalizer) -> None:
        result = normalizer.normalize("HTTPS://WWW.EXAMPLE.COM/Path")
        assert result is not None
        assert result.startswith("https://www.example.com/")
        # Path case is preserved
        assert "/Path" in result


# --- Step 3: Remove default ports ---


class TestRemoveDefaultPorts:
    """normalize() removes port 80 for http, 443 for https."""

    def test_removes_port_80_for_http(self, normalizer: URLNormalizer) -> None:
        result = normalizer.normalize("http://example.com:80/path")
        assert result == "http://example.com/path"

    def test_removes_port_443_for_https(self, normalizer: URLNormalizer) -> None:
        result = normalizer.normalize("https://example.com:443/path")
        assert result == "https://example.com/path"

    def test_keeps_non_default_port(self, normalizer: URLNormalizer) -> None:
        result = normalizer.normalize("http://example.com:8080/path")
        assert result == "http://example.com:8080/path"

    def test_keeps_port_443_for_http(self, normalizer: URLNormalizer) -> None:
        """Port 443 is only default for https, not http."""
        result = normalizer.normalize("http://example.com:443/path")
        assert result is not None
        assert ":443" in result

    def test_keeps_port_80_for_https(self, normalizer: URLNormalizer) -> None:
        """Port 80 is only default for http, not https."""
        result = normalizer.normalize("https://example.com:80/path")
        assert result is not None
        assert ":80" in result


# --- Step 4: Remove fragment ---


class TestRemoveFragment:
    """normalize() strips the fragment component."""

    def test_strips_fragment(self, normalizer: URLNormalizer) -> None:
        result = normalizer.normalize("http://example.com/page#section")
        assert result == "http://example.com/page"

    def test_strips_empty_fragment(self, normalizer: URLNormalizer) -> None:
        result = normalizer.normalize("http://example.com/page#")
        assert result == "http://example.com/page"

    def test_no_fragment_unchanged(self, normalizer: URLNormalizer) -> None:
        result = normalizer.normalize("http://example.com/page")
        assert result == "http://example.com/page"


# --- Step 5: Sort query parameters ---


class TestSortQueryParams:
    """normalize() sorts query parameters by name, then by value."""

    def test_sorts_params_alphabetically(self, normalizer: URLNormalizer) -> None:
        result = normalizer.normalize("http://example.com/?z=1&a=2")
        assert result == "http://example.com/?a=2&z=1"

    def test_sorts_same_key_by_value(self, normalizer: URLNormalizer) -> None:
        result = normalizer.normalize("http://example.com/?a=z&a=a")
        assert result == "http://example.com/?a=a&a=z"

    def test_preserves_blank_values(self, normalizer: URLNormalizer) -> None:
        result = normalizer.normalize("http://example.com/?b=&a=")
        assert result == "http://example.com/?a=&b="

    def test_no_params_no_question_mark(self, normalizer: URLNormalizer) -> None:
        result = normalizer.normalize("http://example.com/path")
        assert result is not None
        assert "?" not in result


# --- Step 6: Uppercase percent-encoded triplets ---


class TestUppercasePercentEncoding:
    """normalize() uppercases hex digits in percent-encoded triplets."""

    def test_lowercase_percent_encoding_uppercased(
        self, normalizer: URLNormalizer
    ) -> None:
        result = normalizer.normalize("http://example.com/path%2f")
        assert result is not None
        assert "%2F" in result

    def test_mixed_case_percent_encoding_uppercased(
        self, normalizer: URLNormalizer
    ) -> None:
        result = normalizer.normalize("http://example.com/path%2a")
        assert result is not None
        assert "%2A" in result


# --- Step 7: Decode unreserved percent-encoded characters ---


class TestDecodeUnreserved:
    """normalize() decodes percent-encoded unreserved characters (RFC 3986 2.3)."""

    def test_decodes_unreserved_alpha(self, normalizer: URLNormalizer) -> None:
        # %61 = 'a' (unreserved)
        result = normalizer.normalize("http://example.com/%61bc")
        assert result is not None
        assert "/abc" in result

    def test_decodes_unreserved_digit(self, normalizer: URLNormalizer) -> None:
        # %31 = '1' (unreserved)
        result = normalizer.normalize("http://example.com/%31%32%33")
        assert result is not None
        assert "/123" in result

    def test_preserves_reserved_encoding(self, normalizer: URLNormalizer) -> None:
        # %2F = '/' (reserved, should stay encoded)
        result = normalizer.normalize("http://example.com/a%2Fb")
        assert result is not None
        assert "%2F" in result


# --- Step 8: Trailing slash handling ---


class TestTrailingSlash:
    """normalize() removes trailing slash for non-root, keeps for root."""

    def test_removes_trailing_slash_non_root(self, normalizer: URLNormalizer) -> None:
        result = normalizer.normalize("http://example.com/path/")
        assert result == "http://example.com/path"

    def test_keeps_root_slash(self, normalizer: URLNormalizer) -> None:
        result = normalizer.normalize("http://example.com/")
        assert result == "http://example.com/"

    def test_bare_domain_gets_root_slash(self, normalizer: URLNormalizer) -> None:
        result = normalizer.normalize("http://example.com")
        assert result == "http://example.com/"

    def test_multiple_trailing_slashes_removed(self, normalizer: URLNormalizer) -> None:
        result = normalizer.normalize("http://example.com/path///")
        assert result is not None
        assert not result.endswith("/")


# --- Composite normalization (multiple steps) ---


class TestCompositeNormalization:
    """Tests exercising multiple normalization steps together."""

    def test_full_normalization(self, normalizer: URLNormalizer) -> None:
        """All steps applied together."""
        url = "HTTP://WWW.EXAMPLE.COM:80/Path/%61?z=1&a=2#frag"
        result = normalizer.normalize(url)
        assert result is not None
        # Scheme lowered, host lowered, port 80 removed, unreserved decoded,
        # query sorted, fragment stripped
        assert result == "http://www.example.com/Path/a?a=2&z=1"

    def test_idempotent_on_already_normalized(self, normalizer: URLNormalizer) -> None:
        url = "https://example.com/path?a=1&b=2"
        first = normalizer.normalize(url)
        assert first is not None
        second = normalizer.normalize(first)
        assert second == first
