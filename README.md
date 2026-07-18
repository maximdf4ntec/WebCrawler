# Web Crawler

A single-process, asynchronous Python crawler built as a pipeline consisting of a persistent URL frontier, scheduler, worker pool, centralized rate limiter, and pluggable content processors. It starts from a seed URL, stays within its domain, and downloads HTML, images, videos, and PDFs through the provided rate-limited fetchUrl() API. Networking concerns (HTTP implementation, browser automation, authentication, proxies, etc.) are intentionally delegated to the provided API, allowing the crawler to focus on orchestration, scheduling, processing, and persistence.

## Key Architectural Decisions

**SQLite as the only store — deliberately simple, not a compromise.** Everything the crawler needs to remember (frontier state, retry/lease bookkeeping, content hashes, discovered-from relationships, per-type metadata) lives in one embedded SQLite file, accessed via `aiosqlite` in WAL mode. There is no Redis, no message queue, no separate metadata database. Since the crawler runs as a single asyncio process, SQLite provides transactional state transitions without requiring Redis, a message broker, or a separate metadata store. Conditional SQL updates naturally implement atomic lease acquisition and completion while keeping the solution self-contained.

**Lease-based at-most-once processing.** Instead of a plain dequeue, each URL acquired for processing gets a lease: a token and an expiry, set via a conditional `UPDATE ... WHERE crawl_state = 'Pending' AND ...`. Workers periodically renew their lease while processing; if a worker dies or is slow, the lease expires and the URL is reclaimed automatically. Every terminal write (`mark_completed`, `mark_retry`, `mark_terminal_failed`) is itself guarded by the lease token, so a stale/crashed worker can never overwrite work a newer worker already did. This guarantees at-most-once processing under concurrent workers without requiring distributed locking.

**Scheduling separation from processing logic.** Scheduling, fetching, and content processing are intentionally separated to keep the orchestration independent from content-specific logic and to simplify future extensibility.

**Content-addressed storage + two-tier dedup.** Every downloaded body is SHA-256 hashed; the hash is both the filename and the change-detection key. This solves URL-level dedup (already-seen normalized URL) and content-level dedup (different URLs, identical bytes) with the same mechanism, and makes re-runs idempotent for free.

**Content processors as a Strategy pattern.** A dispatcher keyed by MIME type (exact match, then prefix match) routes each response to an `HtmlProcessor`/`ImageProcessor`/`VideoProcessor`/`PdfProcessor`. Adding a fifth content type is a new class + one registration line — no changes to the scheduler, worker, or dispatch logic.

**Rate limiting as a single gateway, decoupled from workers.** All Fetch API calls go through one `RateLimiter` that owns concurrency gating, FIFO scheduling, and 429 backoff (`Retry-After` if present, exponential otherwise). Workers don't know or care about rate limits — they just call `execute(fn)`.

## Trade-offs Considered

- **SQLite vs. Postgres/Redis/a broker:** rejected the extra infrastructure because the crawl is single-domain and single-process — there's no producer/consumer split to justify a broker, and a second database would mean two sources of truth to keep consistent. The trade-off is an explicit ceiling: this design assumes one process, one machine.
- **Thread offloading vs. multiprocessing for CPU-bound parsing:** HTML/image/PDF parsing runs via `asyncio.to_thread` rather than a process pool. Simpler (no IPC, no pickling) and sufficient here since the workload is I/O-bound overall (waiting on the rate-limited Fetch API dominates), but it means CPU-heavy parsing still competes for the GIL rather than true parallel execution.
- **Simple priority tiers vs. a full pluggable policy engine:** the frontier orders work by (retry-ready > expired-lease > pending) then crawl depth (BFS-ish), rather than building a configurable scope/priority/politeness policy framework. Enough for this assignment's scope; a real multi-crawl system would likely want that as a separate, swappable component.
- **Polling scheduler rather than event-driven scheduling:** the scheduler periodically polls for runnable work rather than using event-driven notifications.
- **Using hash as a file name:** using hash has significant advantages: Guaranteed uniqueness, natural content deduplication, Idempotent writes, no serialization issues, but it's hardly readable. For production content repository, I'd consider different format like <sanitized-title>_<hash>.html

## At Production Scale

- **State store:** SQLite's single-writer model is the first thing to outgrow — move to Postgres so multiple crawler processes/machines can share one frontier, with the same lease-token pattern (SQL transactions still give the same guarantees).
- **Coordination:** beyond one machine's concurrency ceiling, the in-process lease mechanism would need to become a real distributed queue (SQS/Kafka) or a centrally-coordinated frontier service, since asyncio tasks in one process no longer suffice.
- **Rate limiting:** currently one global limiter, appropriate for one domain. Crawling many domains needs per-domain limiters/backoff state instead of a shared one.
- **Storage:** local filesystem output would move to object storage (S3/GCS) for durability and multi-worker access to the same content-addressed blobs.
- **Observability:** structured logs are enough here; at scale this becomes metrics (URLs/sec, queue depth, failure rates by domain) feeding Prometheus/Grafana, since troubleshooting distributed crawls requires centralized metrics rather than log inspection.
- **Cost:** the main lever is bandwidth and storage for large content (video especially) — at volume, this argues for streaming hash/size checks instead of holding full bodies in memory, and possibly a separate small/large-file processing path.

## What I'd Improve With More Time

- Stream large response bodies (hash and size-check incrementally) instead of holding the full body in memory before processing — matters most for large video files.
- Add `robots.txt` support as another filter step alongside domain/depth/pattern checks.
- Bound graceful shutdown with an explicit timeout rather than waiting unconditionally for in-flight workers to finish.
- Broaden the set of explicitly-handled content types (currently anything outside html/image/video/pdf is treated as unsupported) and make that failure path more visible in the final crawl report.
