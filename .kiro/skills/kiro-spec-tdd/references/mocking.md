# What to mock (and what not to)

## Mock at the true I/O boundary, not at your own abstractions

For a crawler or any networked module, the seam to mock is the actual HTTP client transport —
not your own wrapper classes.

- **Mock**: the underlying HTTP transport (e.g., `httpx.MockTransport`, `responses`,
  `requests-mock`, or a fake socket/session object injected at construction time).
- **Don't mock**: your own `Fetcher.fetch()`, `Parser.parse()`, `Scheduler.enqueue()`, etc.
  Mocking your own class's methods to test a different class that calls it means you're
  testing "did I call the method" instead of "does the behavior hold" — and it silently
  couples the test to today's internal call structure, defeating the point of testing at a
  seam.

If module A calls module B and you're testing A, prefer constructing a real B configured
against fake I/O over replacing B with a `Mock()`. If B is expensive/complex to construct even
with fake I/O, a hand-written fake (a small class implementing B's real interface with
canned behavior) is preferable to a `MagicMock` — a fake still respects B's real contract shape,
where a MagicMock will happily accept calls to methods that don't exist.

## Fixtures for canned responses/payloads

Crawler-style tests need a library of realistic response fixtures. Keep them as actual fixture
files (HTML samples, response payloads) referenced by fixtures/conftest, not inlined as string
literals scattered through test bodies:

- Malformed/truncated HTML
- Redirect chains (including redirect loops, to test loop-detection behavior if design.md
  specifies it)
- Timeouts / connection resets
- Non-200 status codes relevant to the spec (404, 429, 500, 503)
- `robots.txt` variants, if the design's scope includes robots compliance

## Signs you're over-mocking

- The test still passes if you delete the assertion and replace it with `assert True`, as long
  as the mock call assertions stay — meaning the mock assertions were doing all the "work" and
  the real behavior was never checked.
- You need to mock more than 2-3 collaborators to exercise one behavior — usually a sign the
  unit under test has too wide a seam, or you're testing at the wrong boundary (test the
  narrower unit directly instead).
- Mocking a data class or simple value object rather than constructing a real instance.
