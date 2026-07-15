<!-- Based on/adapted from:
     https://github.com/mattpocock/skills/blob/main/skills/engineering/tdd/SKILL.md -->
---
name: tdd
description: Test-driven development with a red-green-refactor loop for Python/pytest. Builds features or fixes bugs one vertical slice at a time. Activates when the user asks to implement a feature, fix a bug, or says "let's TDD this".
---

# TDD Skill

TDD is the red → green loop. This skill is the reference that keeps that loop producing tests worth keeping: what a good test is, where tests go, the anti-patterns, and the rules of the loop. Consult these sections *before and during* the loop, every cycle — not as an afterthought once code exists.

## Before starting
Read `GLOSSARY.md` / `CONTEXT.md` if present (see the `domain-modeling` skill) so test names and fixture vocabulary match the project's actual terms as defined in requirements.md, not ad-hoc synonyms.

## Core principle: test the seam, not the internals
A **seam** is the public boundary you test at — the interface where you observe behavior without reaching inside. Tests verify behavior through public interfaces, not implementation details. The internal code can be rewritten entirely; the test shouldn't need to change if the public contract is unchanged.

A good test reads like a specification: `test_trajectory_with_missing_tool_response_raises_parse_error` tells you exactly what capability/contract exists, and it survives refactors because it doesn't know or care about internal structure.

See `references/tests.md` for concrete good/bad examples and `references/mocking.md` for what to mock in this codebase.

## The loop
1. **Red** — write one failing test for one vertical slice of behavior (e.g., "parsing a single malformed tool_call raises a specific exception," not "parsing works"). Run it. Confirm it fails for the *right* reason (not an import error).
2. **Green** — write the minimum implementation to pass that one test. Resist the urge to build the whole feature; one slice at a time.
3. **Refactor** — with the test green, clean up the implementation (and the test if it's awkward) without changing behavior. Re-run to confirm still green.
4. Repeat for the next slice.

Never write implementation before its test exists — that's not TDD, it's TAD (test-after development), and it tends to produce tests that just describe what the code already does rather than what it should do.

## Rules
- One assertion concept per test. Multiple `assert` calls are fine if they're checking facets of the same behavior; don't bundle unrelated behaviors into one test for convenience.
- Test names describe behavior + condition, not implementation: `test_export_yaml_preserves_nested_assertion_groups`, not `test_export_yaml_2`.
- No conditional logic (`if`/`for`/`try` beyond a single `pytest.raises`) inside a test body — a test with branches is testing multiple things and hiding which one failed.
- Fixtures for shared setup live in `conftest.py` at the narrowest scope that makes sense; don't hoist everything to a root conftest "just in case."
- New public function or CLI flag → new test file/class mirroring the module path, not appended arbitrarily to whatever file was open.

## Anti-patterns to flag on sight
- Asserting on internal state (`obj._cache`) instead of observable output.
- Mocking so much that the test only proves the mocks were called, not that behavior is correct.
- Snapshot tests for anything with a stable, describable contract (fine for genuinely unstructured output; overused elsewhere).
- Tests that pass whether or not the fix is applied (i.e., testing something the implementation was already going to do anyway) — delete or rewrite.
