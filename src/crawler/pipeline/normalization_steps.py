"""Concrete normalization step classes for the URL normalization pipeline.

Each class encapsulates a single normalization transformation, matching
the original logic from ``url_normalizer.py`` exactly. Steps are executed
in the order defined by the ``NORMALIZATION_STEPS`` registry list.
"""

import re
import string
from urllib.parse import parse_qsl, urlencode, urlparse

from .base import NormalizationStep
from .context import NormalizationContext

# ---------------------------------------------------------------------------
# Helper constants and functions (moved from url_normalizer.py)
# ---------------------------------------------------------------------------

# RFC 3986 §2.3 unreserved characters: ALPHA / DIGIT / "-" / "." / "_" / "~"
_UNRESERVED_CHARS: set[str] = set(string.ascii_letters + string.digits + "-._~")

# Pattern to match percent-encoded triplets (e.g. %2f, %2F, %2a)
_PERCENT_ENCODED_RE = re.compile(r"%([0-9a-fA-F]{2})")

# Default ports per scheme
_DEFAULT_PORTS: dict[str, int] = {"http": 80, "https": 443}


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


# ---------------------------------------------------------------------------
# Concrete Normalization Steps
# ---------------------------------------------------------------------------


class ParseURLStep(NormalizationStep):
    """Step 1: Parse URL, reject if invalid (no scheme or no hostname)."""

    name = "parse_url"

    def execute(self, ctx: NormalizationContext) -> NormalizationContext:
        if not ctx.raw_url or not ctx.raw_url.strip():
            ctx.rejected = True
            return ctx

        parsed = urlparse(ctx.raw_url)

        if not parsed.scheme or not parsed.hostname:
            ctx.rejected = True
            return ctx

        ctx.parsed = parsed
        ctx.scheme = parsed.scheme
        ctx.host = parsed.hostname
        ctx.port = parsed.port
        ctx.path = parsed.path
        ctx.query = parsed.query
        return ctx


class LowercaseStep(NormalizationStep):
    """Step 2: Lowercase scheme and host."""

    name = "lowercase"

    def execute(self, ctx: NormalizationContext) -> NormalizationContext:
        ctx.scheme = ctx.scheme.lower()
        ctx.host = ctx.host.lower()
        return ctx


class RemoveDefaultPortStep(NormalizationStep):
    """Step 3: Remove default ports (80 for http, 443 for https)."""

    name = "remove_default_port"

    def execute(self, ctx: NormalizationContext) -> NormalizationContext:
        if ctx.port is not None and _DEFAULT_PORTS.get(ctx.scheme) == ctx.port:
            ctx.port = None
        return ctx


class RemoveFragmentStep(NormalizationStep):
    """Step 4: Remove fragment (already excluded from context by design)."""

    name = "remove_fragment"

    def execute(self, ctx: NormalizationContext) -> NormalizationContext:
        # Fragment is not carried in context — this step is a no-op
        # but exists for explicitness and configurability
        return ctx


class SortQueryParamsStep(NormalizationStep):
    """Step 5: Sort query parameters by name, then by value."""

    name = "sort_query_params"

    def execute(self, ctx: NormalizationContext) -> NormalizationContext:
        params = parse_qsl(ctx.query, keep_blank_values=True)
        params.sort(key=lambda pair: (pair[0], pair[1]))
        ctx.query = urlencode(params)
        return ctx


class UppercasePercentEncodingStep(NormalizationStep):
    """Step 6: Uppercase hex digits in percent-encoded triplets."""

    name = "uppercase_percent_encoding"

    def execute(self, ctx: NormalizationContext) -> NormalizationContext:
        ctx.path = _uppercase_percent_encoding(ctx.path)
        ctx.query = _uppercase_percent_encoding(ctx.query)
        return ctx


class DecodeUnreservedStep(NormalizationStep):
    """Step 7: Decode percent-encoded unreserved characters (RFC 3986 §2.3)."""

    name = "decode_unreserved"

    def execute(self, ctx: NormalizationContext) -> NormalizationContext:
        ctx.path = _decode_unreserved(ctx.path)
        ctx.query = _decode_unreserved(ctx.query)
        return ctx


class TrailingSlashStep(NormalizationStep):
    """Step 8: Remove trailing slash for non-root paths, keep for root."""

    name = "trailing_slash"

    def execute(self, ctx: NormalizationContext) -> NormalizationContext:
        if not ctx.path:
            ctx.path = "/"
        elif ctx.path != "/" and ctx.path.endswith("/"):
            ctx.path = ctx.path.rstrip("/")
        return ctx


# ---------------------------------------------------------------------------
# Step Registry (order defines default pipeline execution order)
# ---------------------------------------------------------------------------

NORMALIZATION_STEPS: list[type[NormalizationStep]] = [
    ParseURLStep,
    LowercaseStep,
    RemoveDefaultPortStep,
    RemoveFragmentStep,
    SortQueryParamsStep,
    UppercasePercentEncodingStep,
    DecodeUnreservedStep,
    TrailingSlashStep,
]
