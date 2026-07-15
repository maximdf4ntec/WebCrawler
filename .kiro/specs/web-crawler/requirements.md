# Requirements Document

## Introduction

This document specifies the requirements for a production-grade web crawler. Starting from a seed URL, the crawler systematically discovers and downloads an entire website's content — HTML pages, images, videos, and PDFs — while processing and persisting each resource along the way.

The crawler uses a mock HTTP Fetch API (`GET http://mock-api.mock.com/fetch?url=<encoded_url>`) and persists all crawl state to a database. It supports concurrency, fault tolerance, resumability, and rate-limit compliance.

---

## Glossary

- **Crawler**: The top-level system coordinating the entire crawl lifecycle.
- **Scheduler**: The component responsible for assigning URLs to workers and managing crawl state transitions.
- **Worker**: An individual concurrent execution unit that fetches and processes a single URL.
- **Worker_Pool**: The managed set of concurrent Workers.
- **Fetch_API**: The external HTTP endpoint `GET http://mock-api.mock.com/fetch?url=<encoded_url>` used to retrieve resources.
- **Content_Dispatcher**: The component that dispatches fetched content to the appropriate type-specific handler.
- **HTML_Processor**: The Content_Dispatcher handler for `text/html` responses.
- **Image_Processor**: The Content_Dispatcher handler for image MIME types.
- **Video_Processor**: The Content_Dispatcher handler for video MIME types.
- **PDF_Processor**: The Content_Dispatcher handler for `application/pdf` responses.
- **URL_Filter**: The component that validates and restricts discovered URLs before they are enqueued.
- **URL_Normalizer**: The component that canonicalizes URLs to prevent duplicate entries.
- **Rate_Limiter**: The centralized component that controls the request rate to the Fetch_API.
- **Metadata_Store**: The persistent database that stores per-URL crawl state, metadata, and the crawl frontier.
- **Page_Store**: The filesystem or object storage that holds fetched content files.
- **Lease**: A time-bounded, exclusive ownership token granting a single Worker the right to process a URL.
- **Seed_URL**: The initial URL provided by the operator from which crawling begins.
- **Seed_Domain**: The registered domain (host + port) extracted from the Seed_URL; the boundary for domain-restricted crawling.
- **Crawl_Depth**: The number of hops from the Seed_URL to a given URL along the discovered link path.
- **Content_Hash**: A SHA-256 hash of raw response body bytes, used for deduplication and change detection.
- **Crawl_State**: The lifecycle state of a URL: `Pending`, `In_Progress`, `Completed`, `Retry`, `Failed`, or `Terminal_Failed`.
- **Transient_Error**: A recoverable error (network timeout, HTTP 429, HTTP 500, truncated download).
- **Permanent_Error**: A non-recoverable error (HTTP 404, HTTP 403, malformed response, unsupported content type).
- **ETag**: An HTTP response header used for cache validation and change detection.

---

## Out of Scope (Fetch_API Responsibilities)

The following concerns are handled by the external Fetch_API and are explicitly **not** the responsibility of this crawler:

- HTTP client implementation (connection pooling, keep-alive, timeouts)
- Browser automation / headless Chrome
- CAPTCHA solving
- Login handling / authentication
- Cookie management
- Proxy rotation
- TLS handling / certificate validation
- ETag / If-Modified-Since conditional requests (unless exposed by fetchUrl headers)
- robots.txt compliance (unless the assignment explicitly requires it)

The crawler treats the Fetch_API as a black box: it passes a URL and receives a response with `statusCode`, `headers`, and `body`. All network-level complexity is abstracted away by the API.

---

## Requirements

---

### Requirement 1: Seed URL Acceptance and Crawl Bootstrap

**User Story:** As an operator, I want to start a crawl by providing a seed URL, so that the Crawler can systematically discover and download the target website's content.

#### Acceptance Criteria

1. THE Crawler SHALL accept a Seed_URL as its primary input parameter before beginning any crawl activity.
2. WHEN a Seed_URL is provided, THE Crawler SHALL extract the Seed_Domain (defined as the host and port combination) from the Seed_URL and restrict all subsequent crawling to URLs sharing that same host and port.
3. WHEN a Seed_URL is provided, THE Scheduler SHALL enqueue the Seed_URL in the Metadata_Store with Crawl_State `Pending` and Crawl_Depth 0 before dispatching any Worker.
4. IF the Seed_URL has a missing scheme, a missing host, or contains unparseable characters, THEN THE Crawler SHALL reject the input with an error message indicating the invalid URL and the reason for rejection, and terminate without performing any fetch.
5. IF the Metadata_Store is unavailable at bootstrap time, THEN THE Crawler SHALL reject the crawl request with an error message indicating database unavailability and terminate without performing any fetch.
6. IF the Metadata_Store already contains the Seed_URL with Crawl_State `Completed` and the stored Content_Hash equals the hash that would be computed from a fresh fetch, THEN THE Scheduler SHALL log that the URL was previously crawled and skip re-fetching it; otherwise THE Scheduler SHALL re-enqueue it for processing.

---

### Requirement 2: Crawl Frontier and URL Queue

**User Story:** As an operator, I want the crawler to maintain a persistent queue of URLs to visit, so that the crawl can be resumed after interruption without losing or duplicating work.

#### Acceptance Criteria

1. THE Metadata_Store SHALL serve as the persistent crawl frontier, storing every discovered URL with its Crawl_State, Crawl_Depth, parent URL, and discovery timestamp.
2. WHEN a new URL is discovered, THE Scheduler SHALL atomically insert it into the Metadata_Store with Crawl_State `Pending` only if no record for the normalized form of that URL already exists; if a record exists, the insert SHALL be silently skipped.
3. WHEN the Crawler restarts after an interruption, THE Scheduler SHALL resume by querying the Metadata_Store for all URLs with Crawl_State `Pending` or `Retry` (whose next retry timestamp has elapsed) or `In_Progress` with an expired Lease, and SHALL dispatch them to Workers before discovering new URLs.
4. WHILE a crawl is active, THE Metadata_Store SHALL enforce that each normalized URL exists in exactly one Crawl_State at any point in time; no URL SHALL appear in two different non-terminal states simultaneously.
5. THE Metadata_Store SHALL persist crawl state to durable storage before acknowledging a write, so that a process crash does not result in loss of any previously acknowledged frontier data.

---

### Requirement 3: URL Normalization and Deduplication

**User Story:** As a developer, I want all discovered URLs to be normalized before storage, so that semantically identical URLs are not processed more than once.

#### Acceptance Criteria

1. THE URL_Normalizer SHALL normalize every discovered URL by: lowercasing the scheme and host, removing default ports (80 for http, 443 for https), removing URL fragments (`#...`), sorting query parameters ascending by name then value, uppercasing percent-encoded triplets, and percent-encoding non-ASCII characters.
2. WHEN two raw URLs resolve to the same normalized URL, THE URL_Normalizer SHALL treat them as identical.
3. WHEN two raw URLs resolve to the same normalized URL, THE Scheduler SHALL enqueue at most one of them in the Metadata_Store.
4. THE Metadata_Store SHALL use the normalized URL as the primary key for all crawl state lookups.
5. WHEN a URL with trailing slash differs from the same URL without trailing slash only by that slash, THE URL_Normalizer SHALL apply a consistent canonical form: trailing slash removed for paths other than the root path (where root path is defined as "/" or empty string).
6. IF a discovered URL cannot be parsed into a valid absolute URL after normalization, THEN THE URL_Normalizer SHALL discard it and log the malformed URL without enqueuing it.

**Correctness Properties:**

- **Idempotence**: For all URLs `u`, `normalize(normalize(u)) == normalize(u)`.
- **Round-trip deduplication**: For all URL pairs `(u1, u2)` where `normalize(u1) == normalize(u2)`, the Metadata_Store SHALL contain exactly one record after both are submitted for enqueueing.

---

### Requirement 4: Concurrency and Atomic URL Leasing

**User Story:** As a developer, I want the scheduler to atomically assign URLs to workers, so that no URL is processed by more than one worker at any time, even under high concurrency.

#### Acceptance Criteria

1. THE Scheduler SHALL use an atomic Lease mechanism to assign each URL to at most one Worker at a time; the lease operation MUST be a single atomic transaction in the Metadata_Store.
2. WHEN a Worker acquires a Lease on a URL, THE Scheduler SHALL update the URL's Crawl_State to `In_Progress` and record the lease owner ID and lease expiration timestamp atomically.
3. WHILE a Lease is held by a Worker, THE Scheduler SHALL not assign the same URL to any other Worker.
4. WHEN a Lease expires before the owning Worker marks the URL complete, THE Scheduler SHALL return the URL's Crawl_State to `Pending` so another Worker can acquire it; IF the original Worker subsequently attempts to write results for that URL, THEN the Metadata_Store SHALL reject the stale write.
5. THE Worker_Pool SHALL execute Workers concurrently, with a configurable maximum concurrency limit (between 1 and 100), so that multiple URLs are fetched and processed in parallel.
6. IF two Workers attempt to acquire a Lease on the same URL simultaneously, THEN exactly one Worker SHALL succeed and the other SHALL receive no work item for that URL.
7. WHEN a Worker determines that processing will exceed the Lease duration, THE Worker SHALL request a lease renewal from the Scheduler; THE Scheduler SHALL extend the Lease by one additional Lease TTL, up to a maximum of 3 consecutive renewals per URL per lease cycle.

**Correctness Properties:**

- **Mutual exclusion**: For all URLs `u` and for all pairs of Workers `(w1, w2)`, it is never the case that both `w1` and `w2` hold an active Lease on `u` at the same time.
- **No URL left behind**: After the crawl completes, every URL that was enqueued SHALL be in a terminal state (`Completed`, `Failed`, or `Terminal_Failed`), with no URL permanently stuck in `In_Progress` due to an expired lease.

---

### Requirement 5: HTTP Fetch via Fetch API

**User Story:** As a developer, I want all HTTP requests to go through the Fetch_API, so that content retrieval is consistent and the system can handle all defined response types.

#### Acceptance Criteria

1. WHEN a Worker processes a URL, THE Worker SHALL issue a `GET` request to `http://mock-api.mock.com/fetch?url=<percent_encoded_url>` and interpret the response according to its `statusCode`, `headers`, and `body` fields.
2. WHEN the Fetch_API returns `statusCode` 200, THE Worker SHALL pass the response body and headers to the Content_Dispatcher.
3. WHEN the Fetch_API returns `statusCode` 301 or 302, THE Worker SHALL extract the `Location` header, normalize the redirect target URL, and apply URL_Filter rules; IF the URL passes filtering, THEN THE Worker SHALL enqueue it as a new `Pending` URL at the same Crawl_Depth as the source (lease.depth) with the redirect source recorded as parent_url and a redirect_count incremented by 1; IF the redirect_count exceeds the configured maximum (default: 5), THEN THE Worker SHALL mark the URL as `Terminal_Failed` with reason "redirect loop detected"; IF the `Location` header is missing or empty, THEN THE Worker SHALL mark the URL as `Terminal_Failed` with reason "missing redirect location".
4. WHEN the Fetch_API returns `statusCode` 404, THE Worker SHALL mark the URL as `Terminal_Failed` with reason "not found"; WHEN the Fetch_API returns `statusCode` 403, THE Worker SHALL mark the URL as `Terminal_Failed` with reason "blocked".
5. WHEN the Fetch_API returns `statusCode` 500 or a network-level error (connection refused, DNS resolution failure, or request timeout exceeding the configured timeout), THE Worker SHALL treat the failure as a Transient_Error and trigger the retry logic defined in Requirement 8.
6. WHEN the Fetch_API returns `statusCode` 429, THE Rate_Limiter SHALL apply backoff as specified in Requirement 6 before the Worker retries the request.
7. IF the Fetch_API response body is `null` for a 200 response, THEN THE Worker SHALL record the URL as `Terminal_Failed` with reason "empty body".
8. IF the Fetch_API returns a `statusCode` not in the set {200, 301, 302, 403, 404, 429, 500}, THEN THE Worker SHALL mark the URL as `Terminal_Failed` with reason "unexpected status code: <code>".
9. IF the Fetch_API response cannot be parsed as valid JSON or does not contain the required `statusCode`, `headers`, and `body` fields, THEN THE Worker SHALL treat it as a Transient_Error and trigger retry logic.

---

### Requirement 6: Rate Limiting and 429 Backoff

**User Story:** As an operator, I want the crawler to respect the Fetch_API's rate limits, so that the crawler does not get blocked and avoids overloading the API.

#### Acceptance Criteria

1. THE Rate_Limiter SHALL be the single centralized gateway through which all Workers submit Fetch_API requests; no Worker SHALL invoke the Fetch_API directly outside of the Rate_Limiter.
2. WHILE the Rate_Limiter's request capacity is exhausted, THE Rate_Limiter SHALL queue incoming Worker requests (up to a maximum of 1000 queued requests) and release them in FIFO order as capacity becomes available.
3. IF the Rate_Limiter queue reaches 1000 pending requests, THEN THE Rate_Limiter SHALL reject additional incoming requests with an error indicating queue overflow until queue capacity becomes available.
4. WHEN the Fetch_API returns `statusCode` 429, THE Rate_Limiter SHALL pause all outgoing requests for a backoff duration and then resume by dispatching queued requests in FIFO order.
5. IF the 429 response includes a `Retry-After` header with a value between 1 and 300 seconds, THEN THE Rate_Limiter SHALL use that value as the backoff duration; IF the value exceeds 300 seconds, THEN THE Rate_Limiter SHALL cap the backoff at 300 seconds.
6. IF the Fetch_API returns `statusCode` 429 and no `Retry-After` header is present, THEN THE Rate_Limiter SHALL apply exponential backoff starting at 1 second, doubling on each consecutive 429, up to a maximum of 60 seconds.
7. IF a single request receives 10 consecutive 429 responses, THEN THE Rate_Limiter SHALL discard that request and notify the originating Worker with a rate-limit exhaustion error; the Worker SHALL then treat the URL as a Transient_Error.
8. WHILE a Rate_Limiter backoff is active, THE Rate_Limiter SHALL not dispatch any new requests to the Fetch_API.

**Correctness Properties:**

- **Backoff monotonicity**: For consecutive 429 responses without a `Retry-After` header, each successive backoff delay SHALL be greater than or equal to the previous delay, up to the 60-second cap.

---

### Requirement 7: URL Filtering

**User Story:** As a developer, I want discovered URLs to be filtered before enqueuing, so that the crawler stays within defined boundaries and does not waste resources on out-of-scope content.

#### Acceptance Criteria

1. WHEN a URL is discovered, THE URL_Filter SHALL reject any URL whose registered domain does not match the registered domain of the Seed_Domain; such URLs SHALL not be enqueued.
2. WHEN a URL is discovered, THE URL_Filter SHALL reject any URL whose scheme is not `http` or `https`; schemes such as `mailto:`, `javascript:`, and `ftp:` SHALL be discarded.
3. WHERE a maximum Crawl_Depth is configured, THE URL_Filter SHALL reject any URL whose computed Crawl_Depth would exceed the configured maximum.
4. WHERE include or exclude pattern rules are configured, THE URL_Filter SHALL evaluate exclude patterns before include patterns: a URL matching an exclude pattern SHALL be discarded regardless of include patterns; a URL matching no include pattern SHALL also be discarded.
5. WHEN a URL has already been recorded in the Metadata_Store with any Crawl_State (compared by normalized URL with fragment removed), THE URL_Filter SHALL not re-enqueue it (deduplication gate).
6. WHEN a URL contains a fragment identifier, THE URL_Filter SHALL strip the fragment before performing any filter or deduplication check.

**Correctness Properties:**

- **Domain restriction invariant**: After any number of crawl iterations, all URLs in the Metadata_Store with Crawl_State other than `Terminal_Failed` (due to off-domain redirect) SHALL have the same registered domain as the Seed_Domain.

---

### Requirement 8: Resilience and Retry with Exponential Backoff

**User Story:** As an operator, I want transient failures to be retried automatically, so that temporary network issues or server errors do not permanently block content from being crawled.

#### Acceptance Criteria

1. WHEN a Worker encounters a Transient_Error (network timeout, HTTP 500, HTTP 429 exhaustion, or truncated download), THE Scheduler SHALL update the URL's Crawl_State to `Retry` and schedule a retry attempt after an exponential backoff delay.
2. WHEN a URL is scheduled for `Retry`, THE Scheduler SHALL compute the backoff delay as `min(base_delay × 2^(retry_count − 1), max_delay)` where `base_delay` is 1 second and `max_delay` is 5 minutes; THE Scheduler SHALL NOT dispatch the URL to a Worker before the computed backoff delay has elapsed.
3. THE Metadata_Store SHALL record the retry count and next retry timestamp for every URL in `Retry` state.
4. WHEN a URL's retry count reaches the configured maximum (default: 3), THE Scheduler SHALL transition the URL to `Failed` state and SHALL not retry it further.
5. IF a URL in `Failed` state is not manually reset (an operator action that sets Crawl_State back to `Pending` and resets retry count to 0), THEN THE Scheduler SHALL leave it in `Failed` state permanently; it SHALL not be re-enqueued automatically.
6. WHEN a Worker encounters a Permanent_Error (HTTP 404, HTTP 403, malformed response, unsupported content type), THE Scheduler SHALL transition the URL to `Terminal_Failed` with a recorded failure reason that includes the error classification and HTTP status code where applicable, and SHALL not schedule a retry.

**Correctness Properties:**

- **Backoff growth**: For a URL retried `n` times (n ≥ 1), the delay before attempt `n+1` SHALL be `≥` the delay before attempt `n`, up to `max_delay`.
- **Retry bound**: For all URLs `u`, the total number of fetch attempts SHALL never exceed `max_retries + 1`.

---

### Requirement 9: Content Type Detection and Processor Dispatch

**User Story:** As a developer, I want content processing to be driven by the HTTP Content-Type header, so that each resource is handled by the correct processor regardless of its URL extension.

#### Acceptance Criteria

1. WHEN a 200 response is received, THE Content_Dispatcher SHALL determine the resource type exclusively from the `Content-Type` response header; URL path extension SHALL NOT be used for type selection.
2. WHEN the `Content-Type` header is `text/html` regardless of any additional parameters (e.g., charset), THE Content_Dispatcher SHALL dispatch the response to the HTML_Processor.
3. WHEN the `Content-Type` header begins with `image/` regardless of any additional parameters, THE Content_Dispatcher SHALL dispatch the response to the Image_Processor.
4. WHEN the `Content-Type` header begins with `video/` regardless of any additional parameters, THE Content_Dispatcher SHALL dispatch the response to the Video_Processor.
5. WHEN the `Content-Type` header is `application/pdf` regardless of any additional parameters, THE Content_Dispatcher SHALL dispatch the response to the PDF_Processor.
6. IF the `Content-Type` header does not match any supported type or is absent from the response, THEN THE Content_Dispatcher SHALL mark the URL as `Terminal_Failed` with reason "unsupported content type" and SHALL not persist any body content.
7. THE Content_Dispatcher SHALL dispatch responses based on a registered processor table keyed by MIME type prefix; adding a new content type handler SHALL require only adding a new entry to that table without modifying existing processor implementations.

---

### Requirement 10: HTML Processing

**User Story:** As an operator, I want HTML pages to be parsed for links and metadata, so that the crawler can discover new URLs and capture page-level information.

#### Acceptance Criteria

1. WHEN the HTML_Processor handles a response, THE HTML_Processor SHALL parse the response body as HTML (assuming UTF-8 encoding; if the body is not valid UTF-8, THE HTML_Processor SHALL attempt to decode using the charset specified in the Content-Type header, falling back to Latin-1) and extract all `href` attributes from `<a>` tags and all `src` attributes from `<img>`, `<video>`, and `<script>` tags.
2. WHEN the HTML_Processor handles a response, THE HTML_Processor SHALL extract the page title from the `<title>` element.
3. IF no `<title>` element is present in the HTML document, THEN THE HTML_Processor SHALL record an empty string as the title.
4. WHEN the HTML_Processor handles a response, THE HTML_Processor SHALL count the total number of extracted URLs (from all `href` and `src` attributes combined) and record that count in the Metadata_Store alongside the URL record.
5. WHEN the HTML_Processor extracts links, THE HTML_Processor SHALL resolve each relative URL against the base URL of the page before passing it to the URL_Filter; IF a relative URL cannot be resolved to a valid absolute HTTP or HTTPS URL, THEN THE HTML_Processor SHALL discard it and log the unresolvable reference.
6. THE HTML_Processor SHALL persist the raw HTML body to `output/html/` as a file named `<Content_Hash>.html`.

**Correctness Properties (Parser Round-Trip):**

- **Link extraction idempotence**: For all HTML documents `d`, parsing `d` twice SHALL produce the same set of extracted links.
- **Relative URL resolution**: For all base URLs `b` and relative references `r`, `resolve(b, r)` SHALL produce an absolute URL that is a valid HTTP or HTTPS URL; applying `resolve(b, r)` to an already-absolute URL SHALL return that URL unchanged.

---

### Requirement 11: Image Processing

**User Story:** As an operator, I want image resources to be downloaded and their dimensions and size extracted, so that I have structured metadata about every image on the site.

#### Acceptance Criteria

1. WHEN the Image_Processor handles a response with a Content-Type indicating an image type, THE Image_Processor SHALL decode the image to extract its pixel width and height.
2. WHEN the Image_Processor handles a response, THE Image_Processor SHALL record the file size in bytes (from `Content-Length` header if present, otherwise from the length of the response body).
3. IF the image body cannot be decoded to extract dimensions (e.g., corrupt or unsupported sub-format), THEN THE Image_Processor SHALL record width and height as `null` and SHALL log a description of the decode error type as the failure reason.
4. IF the image body cannot be decoded, THEN THE Image_Processor SHALL still persist the raw image file to Page_Store.
5. WHEN the Image_Processor successfully processes a response, THE Image_Processor SHALL persist the raw image body to `output/images/` as a file named `<Content_Hash>.<derived_extension>` where the Content_Hash is the SHA-256 lowercase hexadecimal hash of the body and the extension is derived from the `Content-Type` header (falling back to `bin` if the Content-Type cannot be mapped to a known extension); IF a file with that name already exists, THE Image_Processor SHALL skip writing.
6. WHEN the Image_Processor completes processing, THE Metadata_Store SHALL store the extracted width, height, and file size for the image URL record.

---

### Requirement 12: Video Processing

**User Story:** As an operator, I want video resources to be downloaded and their file size and duration captured, so that I have structured metadata about every video on the site.

#### Acceptance Criteria

1. IF the `Content-Length` header is present in the response, THEN THE Video_Processor SHALL record the file size in bytes from the `Content-Length` header value.
2. IF the `Content-Length` header is absent from the response, THEN THE Video_Processor SHALL record the file size in bytes from the length of the response body.
3. IF duration metadata is available in the response (via a custom header such as `X-Duration` or embedded container metadata), THEN THE Video_Processor SHALL extract and record the duration in seconds.
4. IF duration metadata is not available in the response, THEN THE Video_Processor SHALL record duration as unavailable (null) in the Metadata_Store.
5. WHEN the Video_Processor persists the video body, THE Video_Processor SHALL write it to `output/videos/` as a file named `<Content_Hash>.<derived_extension>` where the extension is derived from the `Content-Type` header; IF the Content-Type cannot be mapped to a known extension, THEN THE Video_Processor SHALL use `bin` as the fallback extension.
6. IF the video download is truncated or the body length does not match the `Content-Length` header, THEN THE Video_Processor SHALL treat the response as a Transient_Error and trigger retry logic.
7. WHEN the Video_Processor completes processing, THE Metadata_Store SHALL store the file size and duration (or null) for the video URL record.

---

### Requirement 13: PDF Processing

**User Story:** As an operator, I want PDF resources to be downloaded and their page count and document title extracted, so that I have structured metadata about every PDF on the site.

#### Acceptance Criteria

1. WHEN the PDF_Processor handles a response, THE PDF_Processor SHALL parse the PDF body to extract the total page count.
2. WHEN the PDF_Processor handles a response, THE PDF_Processor SHALL extract the document title from the PDF metadata fields; if no title is present, THE PDF_Processor SHALL record an empty string.
3. IF the PDF body cannot be parsed (corrupt file or unrecognized format), THEN THE PDF_Processor SHALL record page count as `null`, title as `null`, and SHALL still persist the raw PDF file and log the parse failure reason.
4. THE PDF_Processor SHALL persist the raw PDF body to `output/pdfs/` as a file named `<Content_Hash>.pdf`.
5. WHEN the PDF_Processor completes processing, THE Metadata_Store SHALL store the extracted page count and document title for the PDF URL record.

---

### Requirement 14: Content Hashing, Deduplication, and Change Detection

**User Story:** As a developer, I want every fetched resource to have a content hash stored alongside it, so that duplicate content is stored only once and content changes are detectable on subsequent crawls.

#### Acceptance Criteria

1. WHEN a resource is successfully fetched and its body is non-null, THE Content_Dispatcher SHALL compute a SHA-256 Content_Hash of the raw response body bytes before any processing.
2. WHEN a URL is successfully processed, THE Metadata_Store SHALL store the Content_Hash for that URL record.
3. WHEN a URL is re-crawled and a stored Content_Hash exists for that URL, THE Content_Dispatcher SHALL compare the new Content_Hash with the stored Content_Hash; IF they are equal, THEN THE Content_Dispatcher SHALL skip re-persisting the file to Page_Store and SHALL update only the last crawl timestamp.
4. WHEN a URL is re-crawled and the Content_Hash has changed, THE Content_Dispatcher SHALL persist the new file to Page_Store and update the Content_Hash and last crawl timestamp in the Metadata_Store; the old file SHALL be retained in Page_Store (it may be referenced by other URL records via content-addressed storage).
5. THE Page_Store SHALL name files as `<Content_Hash>.<extension>` where the extension is derived from the `Content-Type` header; two resources with identical content SHALL share the same file on disk, implementing natural content-addressed deduplication.
6. WHEN a URL is crawled for the first time (no prior Content_Hash exists), THE Content_Dispatcher SHALL persist the file unconditionally and store the computed Content_Hash.
7. WHERE an `ETag` header is present in the Fetch_API response, THE Metadata_Store SHALL store its value alongside the URL record for use in future conditional requests.

**Correctness Properties:**

- **Hash determinism**: For all byte sequences `b`, `sha256(b)` SHALL always produce the same 64-character hexadecimal string.
- **Change detection correctness**: For all URL records `u`, if `new_hash(u) == stored_hash(u)` then the content has not changed; if `new_hash(u) != stored_hash(u)` then the file SHALL be re-persisted.
- **Content-addressed storage invariant**: For all pairs of resources `(r1, r2)`, if `Content_Hash(r1) == Content_Hash(r2)` then exactly one file SHALL exist in Page_Store for both.

---

### Requirement 15: Output Directory Structure

**User Story:** As an operator, I want crawled content stored in a predictable directory structure organized by type, so that I can easily find and process downloaded files.

#### Acceptance Criteria

1. WHEN the Crawler starts, THE Crawler SHALL create the output directory structure (`output/html/`, `output/images/`, `output/videos/`, `output/pdfs/`) before any Worker begins processing; IF any directory already exists, THE Crawler SHALL leave it intact.
2. IF the output directory or any required subdirectory cannot be created (e.g., permission denied, disk full), THEN THE Crawler SHALL halt with an error indication and SHALL not dispatch any Worker.
3. THE HTML_Processor SHALL write all HTML files exclusively to `output/html/`.
4. THE Image_Processor SHALL write all image files exclusively to `output/images/`.
5. THE Video_Processor SHALL write all video files exclusively to `output/videos/`.
6. THE PDF_Processor SHALL write all PDF files exclusively to `output/pdfs/`.
7. IF a Content_Dispatcher encounters a content type that has no designated output subdirectory, THEN THE Content_Dispatcher SHALL mark the URL as `Terminal_Failed` with reason "unsupported content type" and SHALL not write any file.

---

### Requirement 16: Metadata Storage Schema

**User Story:** As a developer, I want a well-defined database schema for all crawl metadata, so that the system can reliably query and update crawl state, frontier, and content metadata.

#### Acceptance Criteria

1. THE Metadata_Store SHALL persist the following fields for every URL record: `url` (raw string, maximum 2048 characters), `normalized_url` (primary key, maximum 2048 characters), `crawl_state` (one of: `Pending`, `In_Progress`, `Completed`, `Retry`, `Failed`, `Terminal_Failed`), `lease_owner_id`, `lease_expires_at`, `retry_count` (non-negative integer), `crawl_depth` (non-negative integer), `parent_url` (maximum 2048 characters, nullable), `content_type`, `content_hash`, `last_crawl_timestamp`, and `failure_reason` (maximum 1024 characters, nullable).
2. THE Metadata_Store SHALL persist content-type-specific metadata in associated records: for HTML — `page_title`, `link_count`; for images — `width`, `height`, `file_size_bytes`; for videos — `file_size_bytes`, `duration_seconds`; for PDFs — `page_count`, `document_title`.
3. THE Metadata_Store SHALL persist crawl configuration (Seed_URL, Seed_Domain, max depth, max retries, concurrency limit) in a dedicated record isolated from URL records, so that URL records can be queried without referencing crawl configuration fields.
4. WHEN a URL record transitions between Crawl_States, THE Metadata_Store SHALL apply the transition atomically such that if two concurrent writers attempt to transition the same URL record simultaneously, exactly one transition succeeds and the other receives a conflict indication, with the URL record left in a consistent single state.
5. THE Metadata_Store SHALL return query results for all URLs filtered by a given Crawl_State within 500 milliseconds for a dataset of up to 1,000,000 URL records.
6. WHEN the Scheduler requests the next batch of `Pending` or `Retry` URLs, THE Metadata_Store SHALL return between 1 and 500 URL records per query, where the batch size is configurable at crawl-configuration time.

---

### Requirement 17: Resumability

**User Story:** As an operator, I want to stop the crawler at any time and resume it later without re-fetching already-completed URLs, so that long crawls are not lost due to interruptions.

#### Acceptance Criteria

1. WHEN the Crawler starts and the Metadata_Store contains existing records for the configured Seed_URL with at least one URL in a non-terminal processable state (`Pending`, `Retry`, or `In_Progress` with expired Lease), THE Scheduler SHALL resume from the existing state rather than resetting it.
2. WHEN the Crawler resumes, THE Scheduler SHALL reset ALL URLs with Crawl_State `In_Progress` back to `Pending` (since no workers from the previous process survive a crash), clearing their lease_owner_id, lease_token, and lease_expires_at fields.
3. WHEN the Crawler resumes, THE Scheduler SHALL not re-enqueue any URL with Crawl_State `Completed` or `Terminal_Failed`.
4. WHEN the Crawler resumes, THE Scheduler SHALL prioritize all `Retry` URLs whose next retry timestamp has elapsed, dispatching them to Workers before processing `Pending` URLs.
5. THE Crawler SHALL produce identical final results (same set of Completed URLs with the same `normalized_url`, `content_hash`, `content_type`, and type-specific metadata fields) whether it completes in a single run or across multiple interrupted and resumed runs, given the same Seed_URL, crawl configuration, and deterministic Fetch_API responses.

---

### Requirement 18: Observability and Logging

**User Story:** As an operator, I want meaningful logs and inspectable crawl state, so that I can monitor progress, diagnose issues, and verify the crawler is working correctly.

#### Acceptance Criteria

1. WHEN a URL transitions between Crawl_States (including transitions caused by lease expiry), THE Crawler SHALL emit a structured log entry including: `normalized_url`, `previous_state`, `new_state`, `timestamp`, and `worker_id`.
2. THE Crawler SHALL emit a progress summary at configurable intervals (between 1 and 3600 seconds, default: every 10 seconds) that includes: total URLs discovered, total URLs completed, total URLs failed, total URLs pending, and current active Worker count.
3. WHEN a Transient_Error occurs, THE Crawler SHALL log the error type, the affected URL, the retry count, and the computed backoff delay in seconds.
4. WHEN a Permanent_Error occurs, THE Crawler SHALL log the error type, the affected URL, and the failure reason (one of: not-found, unsupported-type, malformed-response, blocked).
5. WHEN a rate-limit (429) response is received, THE Crawler SHALL log the backoff duration in seconds being applied and the number of queued requests.
6. THE Metadata_Store SHALL support a query interface that reports the count of URLs in each Crawl_State and returns results within 5 seconds for datasets of up to 1,000,000 URL records.

---

### Requirement 19: Crawl Policy Configuration

**User Story:** As an operator, I want to configure crawl boundaries and behavior, so that I can control the scope and resource usage of the crawler.

#### Acceptance Criteria

1. THE Crawler SHALL accept configuration for: Seed_URL (required), maximum Crawl_Depth (default: unlimited, range: 1 to 1000), maximum concurrent Workers (default: 5, range: 1 to 100), maximum retry count (default: 3, range: 0 to 10), maximum content size in bytes (default: 50 MB, range: 1 KB to 1 GB), and URL include/exclude patterns (default: none); configuration SHALL be read from a YAML file; IF any numeric value falls outside its valid range, THEN THE Crawler SHALL reject the configuration with an error indicating which parameter is invalid and its valid range.
2. IF the `Content-Length` header is present and its value exceeds the configured maximum content size, THEN THE Worker SHALL mark the URL as `Terminal_Failed` with reason "content too large" without downloading the body; IF the `Content-Length` header is absent, THEN THE Worker SHALL proceed with the download and check the body size after receipt — if the received body exceeds the maximum, the Worker SHALL discard the body, mark the URL as `Terminal_Failed` with reason "content too large", and delete any partially written file.
3. WHERE a maximum Crawl_Depth is configured, THE URL_Filter SHALL not enqueue any URL whose Crawl_Depth is strictly greater than the configured maximum.
4. WHEN the Crawler starts a new crawl, THE Crawler SHALL read configuration from a YAML file, validate it, and freeze the active configuration into the Metadata_Store; WHEN the Crawler resumes an existing crawl, THE Crawler SHALL load and use the frozen configuration from the Metadata_Store, ignoring the YAML file; IF the operator provides a YAML configuration that conflicts with the frozen configuration for an existing crawl, THEN THE Crawler SHALL reject the resume with an error indicating the configuration mismatch.

---

### Requirement 20: Discovery Relationship Tracking

**User Story:** As a developer, I want the crawler to record which URL discovered which other URL, so that the crawl graph can be reconstructed and analyzed.

#### Acceptance Criteria

1. WHEN a URL is enqueued as a result of being discovered on a parent page, THE Metadata_Store SHALL record the `parent_url` (normalized) for that URL.
2. THE Seed_URL SHALL be recorded with a `null` parent_url.
3. WHEN a URL is discovered via a redirect (301/302), THE Metadata_Store SHALL record the redirect source URL as the `parent_url` of the redirect target.
4. THE Metadata_Store SHALL support querying all URLs discovered from a given parent URL, enabling reconstruction of the site's link graph.
