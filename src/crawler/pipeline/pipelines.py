"""Pipeline executor classes for URL normalization and filtering.

NormalizationPipeline runs an ordered list of NormalizationStep instances,
reconstructing a canonical URL from the resulting context.

FilterPipeline runs an ordered list of FilterStep instances with short-circuit
semantics: the first step that returns False terminates evaluation.
"""

import logging
from typing import Optional
from urllib.parse import urlunparse

from .base import FilterStep, NormalizationStep
from .context import FilterContext, NormalizationContext

logger = logging.getLogger(__name__)


class NormalizationPipeline:
    """Executes an ordered list of NormalizationStep instances."""

    def __init__(self, steps: list[NormalizationStep]) -> None:
        self._steps = steps

    def execute(self, raw_url: str) -> Optional[str]:
        """Run all steps in order. Return normalized URL or None if rejected."""
        ctx = NormalizationContext(raw_url=raw_url)
        for step in self._steps:
            try:
                ctx = step.execute(ctx)
            except Exception:
                logger.warning(
                    f"Normalization step '{step.name}' raised for URL: {raw_url}"
                )
                return None
            if ctx.rejected:
                return None
        return self._reconstruct(ctx)

    def _reconstruct(self, ctx: NormalizationContext) -> str:
        """Rebuild URL string from context components."""
        netloc = ctx.host
        if ctx.port is not None:
            netloc = f"{ctx.host}:{ctx.port}"
        return urlunparse((ctx.scheme, netloc, ctx.path, "", ctx.query, ""))


class FilterPipeline:
    """Executes an ordered list of FilterStep instances with short-circuit."""

    def __init__(self, steps: list[FilterStep]) -> None:
        self._steps = steps

    def execute(self, ctx: FilterContext) -> bool:
        """Run steps in order. Return False on first rejection (short-circuit)."""
        for step in self._steps:
            try:
                if not step.execute(ctx):
                    return False
            except Exception:
                logger.warning(f"Filter step '{step.name}' raised for URL: {ctx.url}")
                return False
        return True
