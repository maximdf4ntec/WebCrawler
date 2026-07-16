# What a good test looks like

## Test the seam, not the internals

A seam is the public boundary you test at — the interface where you observe behavior without
reaching inside. The internal code can be rewritten entirely; the test shouldn't need to change
if the public contract is unchanged.

### Good

```python
def test_fetch_raises_timeout_error_after_max_retries_exceeded(fetcher, mock_transport):
    mock_transport.always_time_out()

    with pytest.raises(FetchTimeoutError):
        fetcher.fetch("https://example.com/page")

    assert mock_transport.call_count == fetcher.max_retries
```

This asserts on the public contract: the exception type raised, and an observable count of
attempts (a documented part of the retry contract, not an internal cache or private counter).
It reads as a specification — "if the transport always times out, fetch raises
FetchTimeoutError after max_retries attempts" — and survives a full rewrite of Fetcher's
internals as long as that contract holds.

### Bad — asserts on internal state

```python
def test_fetch_retry_logic(fetcher, mock_transport):
    mock_transport.always_time_out()
    try:
        fetcher.fetch("https://example.com/page")
    except FetchTimeoutError:
        pass
    assert fetcher._retry_count == 3  # private attribute
    assert fetcher._last_backoff == 8.0  # private attribute
```

Reaching into `_retry_count`/`_last_backoff` couples the test to an implementation detail that
could change (e.g., switching to a library that tracks retries differently) without the
observable behavior changing at all.

### Bad — mocks so much it only proves the mock was called

```python
def test_fetch_calls_transport(fetcher, mock_transport):
    fetcher.fetch("https://example.com/page")
    mock_transport.get.assert_called_once()
```

This never checks what `fetch` actually returns or raises — it would pass even if `fetch`
ignored the transport's response entirely and returned garbage.

### Bad — testing multiple things via conditional logic

```python
def test_fetch_handles_various_responses(fetcher, mock_transport):
    for status in [200, 404, 500, 503]:
        mock_transport.set_status(status)
        if status == 200:
            assert fetcher.fetch("url") is not None
        elif status == 404:
            with pytest.raises(NotFoundError):
                fetcher.fetch("url")
        else:
            with pytest.raises(FetchError):
                fetcher.fetch("url")
```

A test with branches is testing multiple behaviors and hiding which one failed. Split into
`test_fetch_returns_content_on_200`, `test_fetch_raises_not_found_error_on_404`,
`test_fetch_raises_fetch_error_on_5xx`.

## Naming

Names describe behavior + condition:
- `test_parse_malformed_html_raises_parse_error` — good
- `test_export_yaml_preserves_nested_assertion_groups` — good
- `test_parser_2` / `test_fetch_works` — bad, tells you nothing about what's being verified or
  why it would fail

## When a snapshot test is actually appropriate

Fine for genuinely unstructured output where there's no simpler describable contract (e.g.,
"the rendered report matches this reference render"). Overused when the contract is actually
simple and describable — e.g., don't snapshot a parsed URL's components when you could assert
`parsed.scheme == "https"` and `parsed.netloc == "example.com"` directly.
