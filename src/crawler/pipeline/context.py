"""Pipeline context dataclasses for URL normalization and filtering.

These dataclasses carry mutable state through the pipeline steps,
allowing each step to read and modify the processing context.
"""

from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import ParseResult


@dataclass
class NormalizationContext:
    """Mutable context passed through the normalization pipeline.

    Each normalization step reads and modifies this context. Steps can
    set ``rejected = True`` to signal that the URL is invalid and abort
    further processing.
    """

    raw_url: str
    parsed: Optional[ParseResult] = None
    scheme: str = ""
    host: str = ""
    port: Optional[int] = None
    path: str = ""
    query: str = ""
    rejected: bool = False


@dataclass
class FilterContext:
    """Context passed through the filter pipeline.

    Contains all information a filter step needs to decide whether
    a URL should be accepted or rejected.
    """

    url: str
    parsed: ParseResult
    depth: int
    seed_domain: str
    max_depth: Optional[int]
    include_patterns: list[str]
    exclude_patterns: list[str]
    store: object  # MetadataStore
