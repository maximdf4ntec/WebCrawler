# Implementation Plan: URL Pipeline Refactor

## Overview

Refactor `url_normalizer.py` and `url_filter.py` from monolithic procedural methods into composable OO pipelines. Each processing step becomes its own class, configured via YAML. The public API remains unchanged so existing tests pass without modification.

## Tasks

- [x] 1. Create abstract bases and context dataclasses
  - [x] 1.1 Create `src/crawler/pipeline/__init__.py` package with exports
    - Create the `pipeline` subpackage under `src/crawler/`
    - _Requirements: 4.1, 4.2_
  - [x] 1.2 Create `src/crawler/pipeline/context.py` with `NormalizationContext` and `FilterContext` dataclasses
    - `NormalizationContext` with fields: raw_url, parsed, scheme, host, port, path, query, rejected
    - `FilterContext` with fields: url, parsed, depth, seed_domain, max_depth, include_patterns, exclude_patterns, store
    - _Requirements: 4.4_
  - [x] 1.3 Create `src/crawler/pipeline/base.py` with `NormalizationStep` and `FilterStep` abstract base classes
    - `NormalizationStep` with abstract `execute(ctx) -> NormalizationContext` and abstract property `name`
    - `FilterStep` with abstract `execute(ctx) -> bool` and abstract property `name`
    - _Requirements: 4.1, 4.2_

- [x] 2. Implement concrete normalization steps
  - [x] 2.1 Create `src/crawler/pipeline/normalization_steps.py` with all 8 normalization step classes
    - `ParseURLStep`: parse URL, reject if empty/whitespace/no scheme/no hostname
    - `LowercaseStep`: lowercase scheme and host
    - `RemoveDefaultPortStep`: remove port 80 for http, 443 for https
    - `RemoveFragmentStep`: no-op step for explicitness
    - `SortQueryParamsStep`: sort query params by name then value
    - `UppercasePercentEncodingStep`: uppercase hex in percent-encoded triplets
    - `DecodeUnreservedStep`: decode unreserved chars per RFC 3986 §2.3
    - `TrailingSlashStep`: remove trailing slash from non-root, ensure root "/" for bare domains
    - Move `_uppercase_percent_encoding` and `_decode_unreserved` helper functions here
    - Define `NORMALIZATION_STEPS` registry list maintaining execution order
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7_
  - [ ]* 2.2 Write unit tests for normalization steps in `tests/unit/test_normalization_steps.py`
    - Test each step class in isolation with manually constructed contexts
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7_

- [x] 3. Implement concrete filter steps
  - [x] 3.1 Create `src/crawler/pipeline/filter_steps.py` with all 6 filter step classes
    - `SchemeCheckStep`: reject non-http/https schemes
    - `DomainMatchStep`: reject if hostname != seed_domain
    - `DepthCheckStep`: reject if depth > max_depth (when configured)
    - `ExcludePatternStep`: reject if URL matches any exclude pattern
    - `IncludePatternStep`: reject if no include pattern matches (when configured)
    - `DeduplicationStep`: reject if URL exists in MetadataStore
    - Define `FILTER_STEPS` registry list maintaining execution order
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6_
  - [ ]* 3.2 Write unit tests for filter steps in `tests/unit/test_filter_steps.py`
    - Test each step class in isolation with manually constructed contexts
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6_

- [x] 4. Create pipeline classes and YAML config loader
  - [x] 4.1 Create `src/crawler/pipeline/pipelines.py` with `NormalizationPipeline` and `FilterPipeline`
    - `NormalizationPipeline.execute(raw_url) -> Optional[str]`: run steps, return None on rejection, reconstruct URL from context on success
    - `FilterPipeline.execute(ctx) -> bool`: run steps with short-circuit on first False
    - Both pipelines wrap step exceptions: normalization returns None, filter returns False
    - _Requirements: 4.3, 2.3, 5.1, 5.2, 6.3, 6.4_
  - [x] 4.2 Create `src/crawler/pipeline/config.py` with `StepConfig`, `NormalizerPipelineConfig`, `FilterPipelineConfig`, `PipelineConfig`
    - Pydantic models for YAML config validation
    - `PipelineConfig.from_yaml(path)` using `yaml.safe_load()`
    - `PipelineConfig.default()` returning all-steps-enabled config
    - Unknown step names logged as warning and ignored
    - _Requirements: 3.1, 3.2, 3.3, 6.1, 6.2_
  - [x] 4.3 Create `src/crawler/pipeline/builder.py` with `build_normalization_pipeline()` and `build_filter_pipeline()` functions
    - Iterate step registries, check config, instantiate enabled steps
    - Steps not mentioned in config default to enabled
    - _Requirements: 5.1, 5.2, 5.3, 5.4_
  - [ ]* 4.4 Write property test for YAML config round-trip in `tests/properties/test_pipeline_config_props.py`
    - **Property 6: YAML Config Round-Trip**
    - **Validates: Requirements 3.3**

- [x] 5. Checkpoint - Verify pipeline infrastructure
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Refactor URLNormalizer facade to use pipeline
  - [x] 6.1 Modify `src/crawler/url_normalizer.py` to delegate to `NormalizationPipeline`
    - Keep `URLNormalizer.normalize()` public signature unchanged
    - Add optional `config_path` parameter to `__init__` (defaults to None → default config)
    - Build pipeline in `__init__`, delegate `normalize()` to `pipeline.execute()`
    - Remove inline normalization logic (now in step classes)
    - _Requirements: 1.1, 1.2_
  - [ ]* 6.2 Write property test for normalization pipeline equivalence in `tests/properties/test_pipeline_equivalence_props.py`
    - **Property 1: Pipeline Equivalence (Normalization)**
    - Compare old implementation output vs new pipeline output for generated URLs
    - **Validates: Requirements 1.2**
  - [ ]* 6.3 Write property test for normalization idempotency
    - **Property 2: Normalization Idempotency**
    - For generated URLs, verify normalize(normalize(u)) == normalize(u)
    - **Validates: Requirements 1.3**

- [x] 7. Refactor URLFilter facade to use pipeline
  - [x] 7.1 Modify `src/crawler/url_filter.py` to delegate to `FilterPipeline`
    - Keep `URLFilter.passes()` public signature unchanged
    - Build filter pipeline in `__init__`, delegate `passes()` to `pipeline.execute()`
    - Remove inline filter logic (now in step classes)
    - _Requirements: 2.1, 2.2_
  - [ ]* 7.2 Write property test for filter pipeline equivalence in `tests/properties/test_pipeline_equivalence_props.py`
    - **Property 3: Pipeline Equivalence (Filter)**
    - Compare old implementation output vs new pipeline output for generated URL/depth pairs
    - **Validates: Requirements 2.2**

- [x] 8. Create default YAML config file
  - [x] 8.1 Create `src/crawler/pipeline/default_config.yaml` with all steps enabled
    - This file documents the default pipeline configuration
    - All normalization and filter steps set to `enabled: true`
    - _Requirements: 3.1, 3.2_

- [x] 9. Checkpoint - Run existing tests to verify backward compatibility
  - Run `tests/unit/test_url_normalizer.py`, `tests/unit/test_url_filter.py`, and `tests/properties/test_url_filter_props.py`
  - All must pass without modification
  - Ensure all tests pass, ask the user if questions arise.
  - _Requirements: 9.1, 9.2, 9.3_

- [x] 10. Update web-crawler design document
  - [x] 10.1 Update `.kiro/specs/web-crawler/design.md` sections 6 and 7
    - Section 6 (URL Normalization): update to reflect pipeline architecture, step classes, and YAML config
    - Section 7 (URL Filtering): update to reflect pipeline architecture, step classes, and YAML config
    - Reference the new `src/crawler/pipeline/` package structure
    - _Requirements: N/A (documentation task)_

- [x] 11. Final checkpoint - Full test suite
  - Run all unit tests and property tests to confirm nothing is broken
  - Ensure all tests pass, ask the user if questions arise.

## Task Dependency Graph

```json
{
  "waves": [
    {
      "name": "Foundation",
      "tasks": ["1"]
    },
    {
      "name": "Steps",
      "tasks": ["2", "3"]
    },
    {
      "name": "Infrastructure",
      "tasks": ["4"]
    },
    {
      "name": "Verify Infrastructure",
      "tasks": ["5"]
    },
    {
      "name": "Facade Refactor",
      "tasks": ["6", "7"]
    },
    {
      "name": "Config & Compatibility",
      "tasks": ["8", "9"]
    },
    {
      "name": "Documentation & Final",
      "tasks": ["10", "11"]
    }
  ]
}
```

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- The refactoring preserves 100% backward compatibility — no external callers need changes
- Property tests validate universal correctness guarantees across random inputs
- Existing tests serve as the primary regression safety net
- Helper functions `_uppercase_percent_encoding` and `_decode_unreserved` move to the steps module but remain importable if needed
