"""URL Filter — filter chain that decides whether a discovered URL should be enqueued.

Implements the filter algorithm from design.md section 7:
1. Strip fragment (defensive — normalizer already handles this)
2. Reject if scheme is not http or https
3. Reject if domain does not match seed_domain
4. Reject if depth > max_depth (when configured)
5. Reject if URL matches any exclude_pattern (re.search)
6. If include_patterns configured: reject if URL matches none
7. Reject if URL already exists in MetadataStore (dedup)
8. Accept
"""

import re
from typing import Optional
from urllib.parse import urlparse


class URLFilter:
    def __init__(
        self,
        seed_domain: str,
        max_depth: Optional[int],
        include_patterns: list[str],
        exclude_patterns: list[str],
        store: object,  # MetadataStore (not yet implemented)
    ) -> None:
        self._seed_domain = seed_domain
        self._max_depth = max_depth
        self._include_patterns = include_patterns
        self._exclude_patterns = exclude_patterns
        self._store = store

    def passes(self, url: str, depth: int) -> bool:
        """Returns True if URL passes all filter checks (short-circuits on first rejection)."""
        parsed = urlparse(url)

        # 1. Strip fragment (already normalized, but defensive)
        # 2. Reject if scheme is not http or https
        if parsed.scheme not in ("http", "https"):
            return False

        # 3. Reject if domain does not match seed_domain
        if parsed.hostname != self._seed_domain:
            return False

        # 4. Reject if depth > max_depth (when configured)
        if self._max_depth is not None and depth > self._max_depth:
            return False

        # 5. Reject if url matches any exclude_pattern
        for pattern in self._exclude_patterns:
            if re.search(pattern, url):
                return False

        # 6. If include_patterns configured: reject if url matches none
        if self._include_patterns:
            if not any(re.search(p, url) for p in self._include_patterns):
                return False

        # 7. Reject if already exists in MetadataStore (dedup check)
        if self._store.exists(url):
            return False

        # 8. Accept
        return True
