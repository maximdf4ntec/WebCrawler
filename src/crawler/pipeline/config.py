"""YAML-driven pipeline configuration models.

Provides Pydantic models for loading and validating pipeline step
configurations from YAML files. Steps not mentioned in config default
to enabled.
"""

import logging
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class StepConfig(BaseModel):
    """Configuration for a single pipeline step."""

    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


class NormalizerPipelineConfig(BaseModel):
    """YAML-driven config for the normalization pipeline."""

    steps: dict[str, StepConfig] = Field(default_factory=dict)


class FilterPipelineConfig(BaseModel):
    """YAML-driven config for the filter pipeline."""

    steps: dict[str, StepConfig] = Field(default_factory=dict)


class PipelineConfig(BaseModel):
    """Top-level config encompassing both pipelines."""

    normalizer: NormalizerPipelineConfig = Field(
        default_factory=NormalizerPipelineConfig
    )
    filter: FilterPipelineConfig = Field(default_factory=FilterPipelineConfig)

    @classmethod
    def from_yaml(cls, path: Path) -> "PipelineConfig":
        """Load and validate pipeline config from a YAML file.

        Raises FileNotFoundError if the file does not exist.
        Raises yaml.YAMLError if the file contains invalid YAML.
        """
        with open(path) as f:
            data = yaml.safe_load(f)
        return cls(**(data or {}))

    @classmethod
    def default(cls) -> "PipelineConfig":
        """Return config that reproduces current behavior (all steps enabled)."""
        return cls()
