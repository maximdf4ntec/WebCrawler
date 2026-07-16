# Stub — implementation pending (Task 2.3)
from typing import Optional


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

    def passes(self, normalized_url: str, depth: int) -> bool:
        """Returns True if URL passes all filter checks."""
        raise NotImplementedError
