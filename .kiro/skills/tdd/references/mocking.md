# Mocking Guidelines — trajectory-to-tests

## What must always be mocked in unit tests
- **Langfuse client** — any test touching `langfuse_integration/` mocks the client (`unittest.mock` or a fixture returning a fake client). No unit test should make a network call.
- **LLM API calls** — if any code path calls an LLM (e.g., for LLM-as-judge soft-rule generation), mock the client response. Real API calls belong only in a separate, explicitly-marked integration test suite (`tests/integration/`, run opt-in, not in default `pytest`).
- **Filesystem writes** — use `tmp_path` (pytest's built-in fixture) instead of mocking `open()` directly where possible; it's more realistic and catches path-construction bugs mocks would hide. Reserve `unittest.mock.patch("builtins.open")` for cases where you specifically need to assert *what* was written without touching disk.
- **Time** — if trajectory timestamps or test-generation timestamps matter, freeze time (`freezegun` or a fixture) rather than asserting against `datetime.now()`.

## What NOT to mock
- Pure parsing/transformation logic (trajectory → intermediate representation → pytest/YAML output) — this is the core value of the library and should be tested with real fixture data, not mocked into meaninglessness.
- Your own dataclasses/schema objects — construct real instances in tests.

## Fixture organization
- Realistic trajectory fixtures (anonymized/synthetic, never real user data) live under `tests/fixtures/trajectories/<format>/`.
- Keep fixtures minimal — the smallest trajectory that exercises the behavior under test. A 200-message fixture for a test about missing-tool-response handling is a maintenance burden, not thoroughness.

## Mock verification anti-pattern
Don't just assert `mock.called` — assert on the *arguments* the mock was called with, or you've only proven the code path was reached, not that it behaved correctly:

```python
# Weak
mock_langfuse.send.assert_called()

# Strong
mock_langfuse.send.assert_called_once_with(trace_id="abc123", status="pass")
```
