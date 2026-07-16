# Requirements Document

## Introduction

This document specifies the requirements for refactoring the URL normalization and filtering subsystems from monolithic procedural methods into composable, object-oriented pipelines. Each processing step becomes an independent class, and a YAML configuration file controls which steps are active and their parameters. The public API (`URLNormalizer.normalize()` and `URLFilter.passes()`) remains backward-compatible.

## Glossary

- **NormalizationPipeline**: An ordered chain of `NormalizationStep` instances that transforms a raw URL into its canonical form
- **FilterPipeline**: An ordered chain of `FilterStep` instances that decides whether a URL should be enqueued
- **NormalizationStep**: An abstract base class defining the contract for a single URL normalization transformation
- **FilterStep**: An abstract base class defining the contract for a single URL filter check
- **NormalizationContext**: A mutable dataclass carrying intermediate state through the normalization pipeline
- **FilterContext**: A dataclass carrying all inputs required by filter steps
- **PipelineConfig**: A Pydantic model representing the YAML-driven configuration for both pipelines
- **StepConfig**: A Pydantic model representing the enabled/disabled state and parameters of a single step
- **Step_Registry**: An ordered list of step classes that defines the default pipeline execution order
- **URLNormalizer**: The public facade class for URL normalization (backward-compatible API)
- **URLFilter**: The public facade class for URL filtering (backward-compatible API)
- **Short_Circuit**: Evaluation strategy where the filter pipeline stops on the first rejection

## Requirements

### Requirement 1: Backward-Compatible Normalization

**User Story:** As a crawler developer, I want the refactored normalizer to produce identical output to the original implementation, so that existing crawl behavior and deduplication remain unchanged.

#### Acceptance Criteria

1. THE URLNormalizer SHALL expose a `normalize(raw_url: str) -> Optional[str]` method with the same signature as the original implementation
2. WHEN the NormalizationPipeline is constructed with default configuration, THE URLNormalizer SHALL produce byte-identical output to the original monolithic implementation for all inputs
3. WHEN `normalize()` is called on a result that is already normalized, THE URLNormalizer SHALL return the same value (idempotency: `normalize(normalize(u)) == normalize(u)`)

### Requirement 2: Backward-Compatible Filtering

**User Story:** As a crawler developer, I want the refactored filter to produce identical decisions to the original implementation, so that URL acceptance behavior remains unchanged.

#### Acceptance Criteria

1. THE URLFilter SHALL expose a `passes(url: str, depth: int) -> bool` method with the same signature as the original implementation
2. WHEN the FilterPipeline is constructed with default configuration, THE URLFilter SHALL produce the same boolean result as the original monolithic implementation for all inputs
3. WHEN a FilterStep rejects a URL, THE FilterPipeline SHALL short-circuit and not execute subsequent steps

### Requirement 3: YAML-Driven Configuration

**User Story:** As a crawler developer, I want to enable, disable, or configure pipeline steps via a YAML file, so that I can customize crawl behavior without modifying code.

#### Acceptance Criteria

1. WHEN a YAML configuration file is provided, THE PipelineConfig SHALL load and validate step configurations using `yaml.safe_load()`
2. WHEN a step is set to `enabled: false` in the YAML config, THE Pipeline SHALL skip that step during execution
3. WHEN a PipelineConfig is serialized to YAML and deserialized back, THE resulting config SHALL be equivalent to the original (round-trip consistency)
4. WHEN all steps are disabled in configuration, THE NormalizationPipeline SHALL return the input URL unchanged and THE FilterPipeline SHALL return True

### Requirement 4: Step Abstraction

**User Story:** As a crawler developer, I want each processing step to be an independent class with a uniform interface, so that steps are individually testable and reusable.

#### Acceptance Criteria

1. THE NormalizationStep abstract base class SHALL define an `execute(ctx: NormalizationContext) -> NormalizationContext` method and a `name` property
2. THE FilterStep abstract base class SHALL define an `execute(ctx: FilterContext) -> bool` method and a `name` property
3. WHEN a NormalizationStep sets `ctx.rejected = True`, THE NormalizationPipeline SHALL abort and return None
4. THE NormalizationContext dataclass SHALL carry mutable intermediate state including raw_url, parsed, scheme, host, port, path, query, and rejected fields

### Requirement 5: Pipeline Composition

**User Story:** As a crawler developer, I want to compose pipelines from an ordered list of step instances, so that execution order is explicit and configurable.

#### Acceptance Criteria

1. THE NormalizationPipeline SHALL execute steps in the order defined by the Step_Registry
2. THE FilterPipeline SHALL execute steps in the order defined by the Step_Registry
3. WHEN a step is not mentioned in the YAML configuration, THE Pipeline SHALL treat it as enabled by default
4. THE Pipeline SHALL support adding new step classes to the Step_Registry without modifying existing pipeline logic

### Requirement 6: Error Handling

**User Story:** As a crawler developer, I want the pipeline to handle errors gracefully, so that a single malformed URL does not crash the crawler.

#### Acceptance Criteria

1. IF the YAML configuration file does not exist or is unreadable, THEN THE PipelineConfig SHALL raise a `FileNotFoundError` at initialization time
2. IF the YAML contains an unrecognized step name, THEN THE PipelineConfig SHALL log a warning and ignore the unknown step
3. IF a NormalizationStep raises an unexpected exception, THEN THE NormalizationPipeline SHALL return None for that URL
4. IF a FilterStep raises an unexpected exception, THEN THE FilterPipeline SHALL return False for that URL

### Requirement 7: Concrete Normalization Steps

**User Story:** As a crawler developer, I want each normalization transformation to be encapsulated in its own step class, so that I can test and configure them independently.

#### Acceptance Criteria

1. THE ParseURLStep SHALL reject URLs that are empty, whitespace-only, or lack a scheme or hostname by setting `ctx.rejected = True`
2. THE LowercaseStep SHALL lowercase both the scheme and host fields of the context
3. THE RemoveDefaultPortStep SHALL set port to None when the port matches the default for the scheme (80 for http, 443 for https)
4. THE SortQueryParamsStep SHALL sort query parameters by name ascending, then by value ascending
5. THE UppercasePercentEncodingStep SHALL uppercase hex digits in all percent-encoded triplets in path and query
6. THE DecodeUnreservedStep SHALL decode percent-encoded unreserved characters per RFC 3986 §2.3 in path and query
7. THE TrailingSlashStep SHALL remove trailing slashes from non-root paths and ensure bare domains have a root "/" path

### Requirement 8: Concrete Filter Steps

**User Story:** As a crawler developer, I want each filter check to be encapsulated in its own step class, so that I can test and configure them independently.

#### Acceptance Criteria

1. THE SchemeCheckStep SHALL return False for URLs with schemes other than "http" or "https"
2. THE DomainMatchStep SHALL return False when the URL hostname does not match the seed_domain
3. WHEN max_depth is configured, THE DepthCheckStep SHALL return False when depth exceeds max_depth
4. THE ExcludePatternStep SHALL return False when the URL matches any configured exclude pattern
5. WHEN include_patterns are configured, THE IncludePatternStep SHALL return False when the URL matches none of the configured patterns
6. THE DeduplicationStep SHALL return False when the URL already exists in the MetadataStore

### Requirement 9: Existing Test Compatibility

**User Story:** As a crawler developer, I want all existing unit and property tests to pass without modification after the refactoring, so that I have confidence the behavior is preserved.

#### Acceptance Criteria

1. WHEN the refactoring is complete, THE existing tests in `test_url_normalizer.py` SHALL pass without modification
2. WHEN the refactoring is complete, THE existing tests in `test_url_filter.py` SHALL pass without modification
3. WHEN the refactoring is complete, THE existing property tests in `test_url_filter_props.py` SHALL pass without modification
