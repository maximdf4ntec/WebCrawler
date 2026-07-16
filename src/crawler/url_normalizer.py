"""URL Normalizer — canonicalizes URLs for deduplication.

Delegates to a composable NormalizationPipeline where each normalization
step is an independent class configured via YAML. The public API
(normalize method signature) remains unchanged.

Key invariant: normalize(normalize(u)) == normalize(u)
"""

from pathlib import Path
from typing import Optional

from .pipeline import PipelineConfig, build_normalization_pipeline


class URLNormalizer:
    """Normalizes URLs into a canonical form for deduplication."""

    def __init__(self, config_path: Optional[Path] = None) -> None:
        """Initialize with optional YAML config path.

        Args:
            config_path: Path to pipeline YAML config file. If None, uses
                default config (all steps enabled, reproducing original behavior).
        """
        if config_path is not None:
            config = PipelineConfig.from_yaml(config_path)
        else:
            config = PipelineConfig.default()
        self._pipeline = build_normalization_pipeline(config)

    def normalize(self, raw_url: str) -> Optional[str]:
        """Returns canonical URL string, or None if unparseable."""
        return self._pipeline.execute(raw_url)
