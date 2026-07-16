"""Concrete filter step implementations.

Each class encapsulates a single filter check from the original
URLFilter.passes() method. Steps read from a FilterContext and
return True (pass) or False (reject).

Execution order is defined by the FILTER_STEPS registry at the
bottom of this module.
"""

import re

from .base import FilterStep
from .context import FilterContext


class SchemeCheckStep(FilterStep):
    """Step 1: Reject if scheme is not http or https."""

    name = "scheme_check"

    def execute(self, ctx: FilterContext) -> bool:
        return ctx.parsed.scheme in ("http", "https")


class DomainMatchStep(FilterStep):
    """Step 2: Reject if domain does not match seed_domain."""

    name = "domain_match"

    def execute(self, ctx: FilterContext) -> bool:
        return ctx.parsed.hostname == ctx.seed_domain


class DepthCheckStep(FilterStep):
    """Step 3: Reject if depth > max_depth (when configured)."""

    name = "depth_check"

    def execute(self, ctx: FilterContext) -> bool:
        if ctx.max_depth is None:
            return True
        return ctx.depth <= ctx.max_depth


class ExcludePatternStep(FilterStep):
    """Step 4: Reject if URL matches any exclude_pattern."""

    name = "exclude_pattern"

    def execute(self, ctx: FilterContext) -> bool:
        for pattern in ctx.exclude_patterns:
            if re.search(pattern, ctx.url):
                return False
        return True


class IncludePatternStep(FilterStep):
    """Step 5: Reject if no include_pattern matches (when configured)."""

    name = "include_pattern"

    def execute(self, ctx: FilterContext) -> bool:
        if not ctx.include_patterns:
            return True
        return any(re.search(p, ctx.url) for p in ctx.include_patterns)


class DeduplicationStep(FilterStep):
    """Step 6: Reject if URL exists in MetadataStore."""

    name = "deduplication"

    async def execute(self, ctx: FilterContext) -> bool:  # type: ignore[override]
        return not await ctx.store.exists(ctx.url)


# Filter step registry — order defines default pipeline execution order.
FILTER_STEPS: list[type[FilterStep]] = [
    SchemeCheckStep,
    DomainMatchStep,
    DepthCheckStep,
    ExcludePatternStep,
    IncludePatternStep,
    DeduplicationStep,
]
