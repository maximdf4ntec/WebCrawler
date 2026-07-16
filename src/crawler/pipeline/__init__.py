"""
URL Pipeline Infrastructure Package.

Provides composable, object-oriented pipelines for URL normalization and filtering.
Each processing step is an independent class configured via YAML.

Public API:
    - NormalizationContext, FilterContext: Dataclasses carrying pipeline state
    - NormalizationStep, FilterStep: Abstract base classes for step implementations
    - NormalizationPipeline, FilterPipeline: Pipeline executors
    - PipelineConfig: YAML-driven configuration loader
    - build_normalization_pipeline, build_filter_pipeline: Pipeline factory functions
"""

# Exports will be populated as submodules are created:
from .context import NormalizationContext, FilterContext
from .base import NormalizationStep, FilterStep

from .pipelines import NormalizationPipeline, FilterPipeline
from .config import PipelineConfig, StepConfig
from .builder import build_normalization_pipeline, build_filter_pipeline
