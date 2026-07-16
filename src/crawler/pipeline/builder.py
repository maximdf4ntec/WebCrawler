"""Pipeline builder functions.

Factory functions that assemble NormalizationPipeline and FilterPipeline
instances from the step registries and a PipelineConfig. Steps not mentioned
in config default to enabled.
"""

import logging

from .base import FilterStep, NormalizationStep
from .config import PipelineConfig, StepConfig
from .filter_steps import FILTER_STEPS
from .normalization_steps import NORMALIZATION_STEPS
from .pipelines import FilterPipeline, NormalizationPipeline

logger = logging.getLogger(__name__)


def build_normalization_pipeline(
    config: PipelineConfig | None = None,
) -> NormalizationPipeline:
    """Build a normalization pipeline from config.

    Iterates over NORMALIZATION_STEPS registry (preserves default order).
    For each step class, check if config enables it.
    If enabled (or not mentioned in config -> default enabled), instantiate.
    """
    if config is None:
        config = PipelineConfig.default()

    steps: list[NormalizationStep] = []
    for step_cls in NORMALIZATION_STEPS:
        step_name = step_cls.name
        step_config = config.normalizer.steps.get(step_name, StepConfig())
        if step_config.enabled:
            steps.append(step_cls())
    return NormalizationPipeline(steps)


def build_filter_pipeline(
    config: PipelineConfig | None = None,
) -> FilterPipeline:
    """Build a filter pipeline from config.

    Iterates over FILTER_STEPS registry (preserves default order).
    For each step class, check if config enables it.
    If enabled (or not mentioned in config -> default enabled), instantiate.
    """
    if config is None:
        config = PipelineConfig.default()

    steps: list[FilterStep] = []
    for step_cls in FILTER_STEPS:
        step_name = step_cls.name
        step_config = config.filter.steps.get(step_name, StepConfig())
        if step_config.enabled:
            steps.append(step_cls())
    return FilterPipeline(steps)
