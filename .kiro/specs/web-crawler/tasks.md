# Implementation Plan: Web Crawler

## Overview

This plan implements a production-grade web crawler in Python 3.12+ using asyncio for concurrency, SQLite (aiosqlite) for state, BeautifulSoup for HTML parsing, and pytest + hypothesis for testing. Tasks are organized to build foundational components first (types, database, utilities), then core orchestration, content processors, and finally integration wiring.

## Tasks

- [x] 1. Project setup and core interfaces
  - [x] 1.1 Initialize Python project with dependencies
    - Create `pyproject.toml` with dependencies: `aiosqlite`, `beautifulsoup4`, `lxml`, `pypdf`, `Pillow`, `httpx`, `pydantic`, `pyyaml`
    - Configure pytest and hypothesis in `pyproject.toml`
    - Add dev dependencies: `pytest`, `pytest-asyncio`, `hypothesis`, `freezegun` or `time-machine`
    - Create directory structure: `src/crawler/`, `src/crawler/processors/`, `tests/properties/`, `tests/unit/`, `tests/integration/`
    - Create `src/crawler/__init__.py` and package structure
    - _Requirements: 19.1_

  - [x] 1.2 Define core types and Pydantic models
    - Create `src/crawler/types.py` with all shared Pydantic BaseModel classes: `CrawlerConfig`, `CrawlResult`, `LeaseResult`, `WorkerResult`, `FetchResponse`, `ProcessorResult`
    - Define type-specific metadata models: `HtmlMetadata`, `ImageMetadata`, `VideoMetadata`, `PdfMetadata`
    - Define `CrawlState` enum with values: `Pending`, `In_Progress`, `Completed`, `Retry`, `Failed`, `Terminal_Failed`
    - Define exception classes: `TransientError`, `PermanentError`, `QueueOverflowError`, `RateLimitExhaustedError`
    - _Requirements: 16.1, 9.1, 5.1_

  - [x] 1.3 Implement Logger module
    - Create `src/crawler/logger.py` with structured logging using Python `logging` module
    - Emit JSON-structured log entries with timestamp, level, message, and context fields
    - Implement `state_transition()`, `progress()`, and error-specific logging methods
    - Implement configurable progress interval logging
    - _Requirements: 18.1, 18.2, 18.3, 18.4, 18.5_

- [x] 2. URL Normalizer and URL Filter
  - [x] 2.1 Implement URL Normalizer
    - Create `src/crawler/url_normalizer.py`
    - Implement normalization steps using `urllib.parse`: lowercase scheme/host, remove default ports, remove fragments, sort query params, uppercase percent-encoded triplets, decode unreserved characters, handle trailing slashes
    - Return `None` for unparseable URLs
    - _Requirements: 3.1, 3.2, 3.4, 3.5, 3.6_

  - [ ]* 2.2 Write property tests for URL Normalizer
    - **Property 1: URL Normalization Idempotence**
    - **Property 2: URL Deduplication**
    - Create `tests/properties/test_url_normalizer_props.py`
    - **Validates: Requirements 3.1, 3.2, 3.3, 3.5**

  - [x] 2.3 Implement URL Filter
    - Create `src/crawler/url_filter.py`
    - Implement filter chain: strip fragment, check scheme (http/https only), check domain match, check depth, check exclude patterns (re.search), check include patterns, check dedup via MetadataStore
    - Exclude patterns take precedence over include patterns
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6_

  - [ ]* 2.4 Write property tests for URL Filter
    - **Property 3: URL Filter Domain/Scheme/Depth Enforcement**
    - **Property 4: Exclude Pattern Precedence**
    - Create `tests/properties/test_url_filter_props.py`
    - **Validates: Requirements 7.1, 7.2, 7.3, 7.4**

- [x] 3. Metadata Store (SQLite)
  - [x] 3.1 Implement Metadata Store initialization and schema
    - Create `src/crawler/metadata_store.py`
    - Implement `init()`: create tables (`crawl_config`, `url_records`, `html_metadata`, `image_metadata`, `video_metadata`, `pdf_metadata`) with indexes
    - Configure SQLite pragmas: WAL mode, busy_timeout=5000, synchronous=NORMAL, foreign_keys=ON
    - Implement `store_config()` and `load_config()` for crawl configuration persistence
    - Use `aiosqlite` for async database access
    - _Requirements: 16.1, 16.2, 16.3, 2.5_

  - [x] 3.2 Implement crawl frontier operations
    - Implement `enqueue()` with INSERT OR IGNORE for atomic deduplication
    - Implement `acquire_lease_batch()` with atomic UPDATE...RETURNING for lease acquisition (priority: Retry → expired In_Progress → Pending, ordered by depth ASC for BFS)
    - Implement `renew_lease()` with max 3 renewals per lease cycle
    - Implement `expire_leases()` to reset expired In_Progress URLs back to Pending
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 4.1, 4.2, 4.3, 4.4, 4.6, 4.7_

  - [x] 3.3 Implement state transitions and queries
    - Implement `mark_completed()` with lease token validation (stale write rejection)
    - Implement `mark_retry()` with retry count and next_retry_at
    - Implement `mark_terminal_failed()` and `mark_failed()`
    - Implement `exists()`, `get_content_hash()`, `get_urls_by_state()`, `get_state_counts()`, `get_child_urls()`
    - Implement type-specific metadata storage: `store_html_metadata()`, `store_image_metadata()`, `store_video_metadata()`, `store_pdf_metadata()`
    - _Requirements: 16.4, 16.5, 16.6, 8.3, 14.2, 20.1, 20.2, 20.3, 20.4_

  - [ ]* 3.4 Write property tests for lease operations
    - **Property 5: Lease Mutual Exclusion**
    - **Property 6: Expired Lease Recovery and Stale Write Rejection**
    - **Property 7: Lease Renewal Bound**
    - Create `tests/properties/test_lease_props.py`
    - **Validates: Requirements 4.1, 4.3, 4.4, 4.6, 4.7**

  - [ ]* 3.5 Write unit tests for Metadata Store
    - Test atomic enqueue deduplication
    - Test lease acquire/release cycle
    - Test state transition atomicity
    - Test config store/load
    - Create `tests/unit/test_metadata_store.py`
    - _Requirements: 2.2, 4.1, 16.4_

- [x] 4. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 5. Rate Limiter
  - [x] 5.1 Implement Rate Limiter with asyncio and 429 backoff
    - Create `src/crawler/rate_limiter.py`
    - Implement asyncio.Queue-based FIFO queue with max capacity 1000
    - Implement `execute()`: queue requests when at capacity, dispatch in FIFO order using asyncio.Semaphore
    - Implement 429 backoff: use Retry-After header (1–300s, capped at 300), or exponential backoff (1s base, doubles, max 60s)
    - Implement consecutive 429 tracking: after 10 consecutive 429s, raise RateLimitExhaustedError
    - Implement global backoff pause: no dispatches while backing off (asyncio.Event)
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8_

  - [ ]* 5.2 Write property tests for Rate Limiter
    - **Property 8: Rate Limiter Backoff Computation**
    - **Property 9: Rate Limiter FIFO Ordering**
    - Create `tests/properties/test_rate_limiter_props.py`
    - **Validates: Requirements 6.2, 6.5, 6.6**

- [ ] 6. Worker and Worker Pool
  - [ ] 6.1 Implement Worker Pool with bounded concurrency
    - Create `src/crawler/worker_pool.py`
    - Implement using asyncio.Semaphore for concurrency control
    - Implement `dispatch()`, `has_capacity()`, `wait_for_slot()`, `active_count()`, `drain()`
    - Use configurable max concurrency (1–100)
    - _Requirements: 4.5_

  - [ ] 6.2 Implement Worker fetch and response handling
    - Create `src/crawler/worker.py`
    - Implement `process_url()`: fetch via Rate Limiter using httpx, handle status codes (200, 301/302, 404, 403, 429, 500, others) using match/case
    - Implement 200 handling: check null body, check content size, compute hash, dispatch to Content Dispatcher, enqueue discovered URLs
    - Implement 301/302 handling: extract Location header, normalize, filter, enqueue redirect target
    - Implement error classification: transient vs permanent
    - Implement lease renewal check before expiration
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 5.9, 19.2_

  - [ ]* 6.3 Write property tests for Worker status handling
    - **Property 17: Unknown Status Code Handling**
    - **Property 19: Content Size Enforcement**
    - Create `tests/properties/test_status_handling_props.py`
    - **Validates: Requirements 5.8, 19.2**

  - [ ]* 6.4 Write property tests for retry logic
    - **Property 10: Retry Backoff Formula**
    - **Property 11: Retry Count Bound**
    - Create `tests/properties/test_retry_props.py`
    - **Validates: Requirements 8.2, 8.4**

- [ ] 7. Content Dispatcher and type-specific processors
  - [ ] 7.1 Implement Content Dispatcher
    - Create `src/crawler/content_dispatcher.py`
    - Implement `BaseProcessor` ABC with abstract `process()` method and shared `compute_hash()` / `write_file_if_not_exists()` helpers
    - Implement `ContentDispatcher` class with `register()` and `dispatch()` methods
    - Dispatch logic: exact MIME match first, then prefix match
    - Unsupported content type → return None (caller marks Terminal_Failed)
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 14.1, 14.3, 14.5, 14.6_

  - [ ]* 7.2 Write property tests for Content Dispatcher dispatch
    - **Property 12: Content Type Dispatch Correctness**
    - **Property 15: Content Hash Determinism**
    - **Property 16: Content-Addressed Storage Invariant**
    - Create `tests/properties/test_content_dispatch_props.py` and `tests/properties/test_content_hash_props.py`
    - **Validates: Requirements 9.1, 9.3, 9.4, 9.6, 14.1, 14.3, 14.5**

  - [ ] 7.3 Implement HTML Processor
    - Create `src/crawler/processors/html_processor.py`
    - Implement `HtmlProcessor` class extending `BaseProcessor`
    - Parse HTML with BeautifulSoup + lxml, extract links from `<a href>`, `<img src>`, `<video src>`, `<script src>`
    - Extract page title from `<title>` element (empty string if absent)
    - Resolve relative URLs using `urllib.parse.urljoin()` against page base URL
    - Persist raw HTML to `output/html/<hash>.html` via inherited `write_file_if_not_exists()`
    - Record link count and page title in Metadata Store
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6_

  - [ ]* 7.4 Write property tests for HTML Processor
    - **Property 13: HTML Link and Title Extraction**
    - **Property 14: Relative URL Resolution**
    - Create `tests/properties/test_html_processor_props.py`
    - **Validates: Requirements 10.1, 10.2, 10.3, 10.4, 10.5**

  - [ ] 7.5 Implement Image Processor
    - Create `src/crawler/processors/image_processor.py`
    - Implement `ImageProcessor` class extending `BaseProcessor`
    - Extract dimensions using `Pillow` (`Image.open(io.BytesIO(body)).size`), null if decode fails
    - Record file size from Content-Length or body length
    - Persist to `output/images/<hash>.<ext>` (ext from Content-Type, fallback `bin`)
    - Store metadata in Metadata Store
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6_

  - [ ] 7.6 Implement Video Processor
    - Create `src/crawler/processors/video_processor.py`
    - Implement `VideoProcessor` class extending `BaseProcessor`
    - Record file size from Content-Length or body length
    - Extract duration from `X-Duration` header if available (null otherwise)
    - Detect truncation: body length < Content-Length → raise TransientError
    - Persist to `output/videos/<hash>.<ext>`
    - Store metadata in Metadata Store
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 12.6, 12.7_

  - [ ] 7.7 Implement PDF Processor
    - Create `src/crawler/processors/pdf_processor.py`
    - Implement `PdfProcessor` class extending `BaseProcessor`
    - Parse PDF with `pypdf` (`PdfReader`): extract page count and document title
    - Handle parse failures: null page count/title, still persist raw file, log error
    - Persist to `output/pdfs/<hash>.pdf`
    - Store metadata in Metadata Store
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5_

- [ ] 8. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 9. Scheduler and Crawler orchestration
  - [ ] 9.1 Implement Scheduler
    - Create `src/crawler/scheduler.py`
    - Implement `init()`: validate config, bootstrap store, seed URL enqueue
    - Implement main loop with asyncio: acquire lease batch → dispatch to worker pool → handle completions → repeat until frontier exhausted
    - Implement retry scheduling with exponential backoff: `min(1.0 * 2**(n-1), 300.0)` seconds
    - Implement lease expiration detection and recovery
    - Implement graceful shutdown: stop dispatching, drain worker pool
    - _Requirements: 1.3, 2.3, 4.1, 4.4, 8.1, 8.2, 8.4, 8.5, 8.6, 17.1, 17.2, 17.3, 17.4_

  - [ ] 9.2 Implement Crawler entry point
    - Create `src/crawler/crawler.py`
    - Implement `start()`: load config from YAML via `CrawlerConfig.from_yaml()`, validate (reject out-of-range params), extract seed domain, create output directories, initialize Metadata Store, freeze config to DB, create Scheduler, run via asyncio
    - Implement `resume()`: load frozen config from DB (ignore YAML), detect config mismatch if YAML also provided, resume from existing state
    - Implement `stop()`: graceful shutdown
    - Validate seed URL: reject missing scheme, missing host, unparseable characters
    - Handle Metadata Store unavailability at bootstrap
    - _Requirements: 1.1, 1.2, 1.4, 1.5, 1.6, 15.1, 15.2, 19.1, 19.4_

  - [ ]* 9.3 Write property tests for configuration validation
    - **Property 18: Configuration Validation**
    - Create `tests/properties/test_config_validation_props.py`
    - **Validates: Requirements 19.1**

- [ ] 10. Integration wiring and end-to-end flow
  - [ ] 10.1 Wire all components together
    - Create `src/crawler/__main__.py` as the CLI entry point (using argparse or click)
    - Instantiate and connect: Crawler → Scheduler → Worker Pool → Workers → Rate Limiter → Content Dispatcher → URL Filter → URL Normalizer → Metadata Store → Logger
    - Implement progress reporting at configurable intervals
    - Ensure output directory creation before worker dispatch
    - _Requirements: 1.1, 6.1, 15.1, 18.2_

  - [ ]* 10.2 Write integration tests for crawl lifecycle
    - Test full crawl: seed → discover children → complete all → verify final state
    - Test resumability: start → interrupt → resume → verify identical final results
    - Test concurrent workers: verify no duplicate processing
    - Test mixed content types: HTML with links to images, videos, PDFs
    - Test error recovery: mix of transient and permanent errors → verify correct final states
    - Create `tests/integration/test_crawl_lifecycle.py`, `tests/integration/test_resumability.py`, `tests/integration/test_concurrent_workers.py`
    - _Requirements: 17.5, 4.1, 9.1_

  - [ ]* 10.3 Write property test for resumability
    - **Property 20: Resumability Equivalence**
    - Create `tests/properties/test_resumability_props.py`
    - **Validates: Requirements 17.5**

- [ ] 11. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- The Fetch API is fully mocked via httpx mock transport — responses defined per URL for determinism
- SQLite uses `:memory:` mode for unit tests, file-based for integration tests
- Filesystem operations use pytest `tmp_path` fixtures (cleaned up automatically)
- Time is mocked via `freezegun` or `time-machine` for backoff and lease expiration tests

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.3"] },
    { "id": 2, "tasks": ["2.1", "3.1"] },
    { "id": 3, "tasks": ["2.2", "2.3", "3.2"] },
    { "id": 4, "tasks": ["2.4", "3.3", "5.1"] },
    { "id": 5, "tasks": ["3.4", "3.5", "5.2", "6.1"] },
    { "id": 6, "tasks": ["6.2", "7.1"] },
    { "id": 7, "tasks": ["6.3", "6.4", "7.2", "7.3", "7.5", "7.6", "7.7"] },
    { "id": 8, "tasks": ["7.4", "9.1"] },
    { "id": 9, "tasks": ["9.2", "9.3"] },
    { "id": 10, "tasks": ["10.1"] },
    { "id": 11, "tasks": ["10.2", "10.3"] }
  ]
}
```
