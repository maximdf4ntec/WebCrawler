# Web Crawler - High-Level Design

## Overview

The crawler starts from a root URL and recursively discovers and processes linked pages. Crawling is performed by a pool of workers using the provided `fetchUrl()` API. The system maintains persistent crawl state to support recovery, avoid duplicate processing, and detect page changes.

The crawler is restricted to the seed domain and guarantees that each URL is processed at most once, even under concurrent execution.

---

## Overall Flow

```text
Root URL
    │
    ▼
Persistent URL Queue
    │
    ▼
Scheduler
    │
    ▼
Worker Pool
    │
    ▼
Rate Limiter
    │
    ▼
fetchUrl()
    │
    ▼
Content Processor
    │
 ├── Extract links
 ├── Compute content hash
 ├── Extract metadata
 └── Persist page
    │
    ▼
URL Filter
    │
    ▼
Normalize & Deduplicate URLs
    │
    ▼
Metadata Storage + Queue new URLs
```

Future extension:

```text
Content Processor
       │
       ▼
 Parse Filter
       │
       ▼
 Metadata
```

The Parse Filter enables additional metadata enrichment and custom processing without modifying the content processors.

---

## URL State Machine

```text
             Lease expires
        ┌────────────────────┐
        │                    │
        ▼                    │
Pending ──Acquire Lease──► In Progress
   ▲                           │
   │                           │
   │                     Success│
   │                           ▼
Retry ◄────Transient────── Completed
   │
   │
Retries < Max
   │
   ▼
 Failed
   │
Retries exhausted /
Permanent failure
   │
   ▼
Terminal Failed
```

Each URL is atomically leased before processing, guaranteeing that only one worker can process it at any given time.

If a worker crashes or becomes unresponsive, the lease expires and the Scheduler returns the URL to the Pending state.

---

## Scheduler

Responsible for coordinating crawl execution.

Responsibilities:

- Atomically assign URLs to workers (lease mechanism)
- Prevent duplicate processing
- Resume interrupted crawls
- Schedule retries with exponential backoff
- Enforce maximum retry count
- Apply crawl policies:
  - Seed-domain restriction
  - Maximum crawl depth
  - Maximum allowed content size
  - Crawl priority

---

## Rate Limiter

Since `fetchUrl()` is rate-limited, all requests pass through a centralized rate limiter.

Responsibilities:

- Enforce API request limits
- Queue requests when capacity is exhausted
- Apply temporary backoff when rate limiting is detected

---

## Content Processor

The processor is selected **only according to the HTTP `Content-Type` returned by `fetchUrl()`**, never from the URL extension.

Example processors:

- HTML
- PDF
- Image
- Video

Responsibilities:

- Extract outgoing links
- Extract metadata
- Compute content hash
- Persist page content
- Submit discovered URLs for further crawling

---

## URL Filter

Executed before enqueueing newly discovered URLs.

Responsibilities:

- Restrict crawling to the seed domain
- Remove unsupported URL schemes
- Apply include/exclude rules
- Enforce maximum crawl depth

---

## Error Handling

### Transient

Examples:

- Network timeout
- Temporary service failure
- Truncated download
- API rate limit exceeded

Action:

- Retry using exponential backoff.

### Permanent

Examples:

- Resource not found
- Unsupported content type
- Malformed or invalid response
- Blocked

Action:

- Mark as Terminal Failed.

---

## Change Detection

Each successfully processed page stores a content hash.

During subsequent crawls, hashes are compared to detect content changes.

(Additional mechanisms such as ETag or Last-Modified could be incorporated in the future if exposed by `fetchUrl()`.)

---

## Storage

### Metadata Storage

Persistent metadata for crawl management.

Stores:

- URL
- Normalized URL
- Crawl state
- Lease owner / lease expiration
- Retry count
- Crawl depth
- Parent URL
- Content type
- Content hash
- Last crawl timestamp

This storage also serves as the persistent crawl frontier (URL queue).

---

### Page Storage

Stores fetched page content.

Suggested file naming strategy:

```
<hash>.<extension>
```

where the extension is derived from the HTTP `Content-Type`.

Examples:

```
2f84c1.html
ac913d.pdf
91aa23.jpg
8d2f10.mp4
```

Using the content hash guarantees unique filenames and naturally supports change detection.

---

### Additional Logical Storage

These are logical components and may be implemented using the same underlying technologies as the Metadata Storage:

- Crawl configuration
- Logs
- Metrics / monitoring

For example, Metadata Storage, Queue, and Configuration may share a single relational database, while Page Storage is implemented as a filesystem or object storage.