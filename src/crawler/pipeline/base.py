"""Abstract base classes for pipeline steps.

Defines the contracts for normalization and filter steps that form
the composable URL processing pipelines.
"""

from abc import ABC, abstractmethod

from .context import FilterContext, NormalizationContext


class NormalizationStep(ABC):
    """Abstract base for a single normalization step."""

    @abstractmethod
    def execute(self, ctx: NormalizationContext) -> NormalizationContext:
        """Transform the context in-place. Set ctx.rejected=True to abort."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this step (used in YAML config)."""
        ...


class FilterStep(ABC):
    """Abstract base for a single filter check."""

    @abstractmethod
    def execute(self, ctx: FilterContext) -> bool:
        """Return True if URL passes this check, False to reject."""
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for this step (used in YAML config)."""
        ...
