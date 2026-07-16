"""URL Filter — filter chain that decides whether a discovered URL should be enqueued.

Delegates to a composable FilterPipeline where each filter check is an
independent class configured via YAML. The public API (passes method signature)
remains unchanged.
"""

from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from .pipeline import PipelineConfig, FilterContext, build_filter_pipeline


class URLFilter:
    def __init__(
        self,
        seed_domain: str,
        max_depth: Optional[int],
        include_patterns: list[str],
        exclude_patterns: list[str],
        store: object,  # MetadataStore
        config_path: Optional[Path] = None,
    ) -> None:
        self._seed_domain = seed_domain
        self._max_depth = max_depth
        self._include_patterns = include_patterns
        self._exclude_patterns = exclude_patterns
        self._store = store

        if config_path is not None:
            config = PipelineConfig.from_yaml(config_path)
        else:
            config = PipelineConfig.default()
        self._pipeline = build_filter_pipeline(config)

    async def passes(self, url: str, depth: int) -> bool:
        """Returns True if URL passes all filter checks (short-circuits on first rejection)."""
        parsed = urlparse(url)
        ctx = FilterContext(
            url=url,
            parsed=parsed,
            depth=depth,
            seed_domain=self._seed_domain,
            max_depth=self._max_depth,
            include_patterns=self._include_patterns,
            exclude_patterns=self._exclude_patterns,
            store=self._store,
        )
        return await self._pipeline.execute(ctx)
