"""URL Normalizer — canonicalizes URLs for deduplication.

Implements the normalization algorithm from design.md section 6:
1. Parse URL — reject if invalid (no scheme or no hostname → return None)
2. Lowercase scheme and host
3. Remove default ports (80 for http, 443 for https)
4. Remove fragment
5. Sort query parameters by name (ascending), then by value
6. Uppercase percent-encoded triplets
7. Decode unreserved percent-encoded characters (RFC 3986 §2.3)
8. Trailing slash: remove for non-root paths, keep for root "/"
9. Reconstruct and return canonical string

Key invariant: normalize(normalize(u)) == normalize(u)
"""

import re
import string
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# RFC 3986 §2.3 unreserved characters: ALPHA / DIGIT / "-" / "." / "_" / "~"
_UNRESERVED_CHARS: set[str] = set(string.ascii_letters + string.digits + "-._~")

# Pattern to match percent-encoded triplets (e.g. %2f, %2F, %2a)
_PERCENT_ENCODED_RE = re.compile(r"%([0-9a-fA-F]{2})")

# Default ports per scheme
_DEFAULT_PORTS: dict[str, int] = {
    "http": 80,
    "https": 443,
}


def _uppercase_percent_encoding(s: str) -> str:
    """Uppercase all hex digits in percent-encoded triplets."""
    return _PERCENT_ENCODED_RE.sub(lambda m: f"%{m.group(1).upper()}", s)


def _decode_unreserved(s: str) -> str:
    """Decode percent-encoded unreserved characters, leave reserved ones encoded.

    After decoding, any remaining percent-encoded triplets are uppercased.
    """

    def _replace(match: re.Match[str]) -> str:
        hex_digits = match.group(1)
        char = chr(int(hex_digits, 16))
        if char in _UNRESERVED_CHARS:
            return char
        # Keep reserved chars encoded but uppercase the hex
        return f"%{hex_digits.upper()}"

    return _PERCENT_ENCODED_RE.sub(_replace, s)


class URLNormalizer:
    """Normalizes URLs into a canonical form for deduplication."""

    def normalize(self, raw_url: str) -> Optional[str]:
        """Returns canonical URL string, or None if unparseable."""
        # Step 1: Parse — reject if no scheme or no hostname
        if not raw_url or not raw_url.strip():
            return None

        parsed = urlparse(raw_url)

        if not parsed.scheme or not parsed.hostname:
            return None

        # Step 2: Lowercase scheme and host
        scheme = parsed.scheme.lower()
        host = parsed.hostname.lower()

        # Step 3: Remove default ports
        port = parsed.port
        if port is not None and _DEFAULT_PORTS.get(scheme) == port:
            port = None

        # Build netloc with optional port
        netloc = host
        if port is not None:
            netloc = f"{host}:{port}"

        # Step 4: Remove fragment (simply don't include it)

        # Step 5: Sort query parameters by name, then by value
        # parse_qsl preserves order and handles edge cases like blank values
        query_params = parse_qsl(parsed.query, keep_blank_values=True)
        query_params.sort(key=lambda pair: (pair[0], pair[1]))
        sorted_query = urlencode(query_params)

        # Get the path
        path = parsed.path

        # Step 6 & 7: Uppercase percent-encoded triplets and decode unreserved chars
        # Apply to path and query. Decoding unreserved also uppercases remaining.
        path = _decode_unreserved(path)
        sorted_query = _decode_unreserved(sorted_query)

        # Step 8: Trailing slash handling
        # - Bare domain (empty path) → "/"
        # - Root "/" → keep "/"
        # - Non-root trailing slash → remove
        if not path:
            path = "/"
        elif path != "/" and path.endswith("/"):
            path = path.rstrip("/")

        # Step 9: Reconstruct
        # urlunparse expects (scheme, netloc, path, params, query, fragment)
        normalized = urlunparse((scheme, netloc, path, "", sorted_query, ""))

        return normalized
