---
name: kiro-test-review
description: >
  Audits a generated pytest test suite for coverage and correctness against a kiro spec
  (.kiro/specs/FEATURE_NAME/design.md and tasks.md) — used as a second, independent pass after
  kiro-spec-tdd generates tests, and before (or after) the implementation pass. Checks whether
  every documented behavior/contract/exception in design.md has a corresponding test (coverage),
  and whether each test actually verifies real behavior rather than being tautological,
  over-mocked, asserting on internals, or encoding an assumption not present in design.md
  (correctness). Activates when the user asks to review, audit, sanity-check, or verify test
  coverage/correctness for a test file or module, asks "are these tests actually good", or asks
  to check generated tests against a spec before trusting them.
---

# Kiro Test Review Skill

This is a review pass, not a generation pass. Its job is to catch two failure modes that a
test-writing pass (including kiro-spec-tdd on its own output) is prone to missing on itself:

1. **Coverage gaps** — design.md documents a behavior/contract that no test exercises.
2. **Correctness problems** — a test exists, runs, and is even green, but doesn't actually prove
   the behavior holds (tautological, over-mocked, asserts on internals, or is built on a
   guessed contract instead of something actually in design.md).

Run this independently from whatever generated the tests — even if that's the same
conversation, treat this as a distinct, skeptical pass rather than folding it into generation.
If gaps or problems are found, **report them; don't silently patch them here.** Fixing tests is
a generation-pass job (route back to kiro-spec-tdd or the user); this skill's job is to tell you
where things stand.

## Inputs needed

- `design.md` and `tasks.md` for the module (same spec folder as the test-writing pass used)
- The test file(s) under review
- The implementation, if it exists yet (not required — this skill works pre-implementation too)

If design.md isn't available, say so and stop — you cannot judge coverage against a spec you
haven't read; don't fall back to judging tests against "what looks reasonable for a crawler" or
similar generic domain conventions.

## Step 1: Build the traceability matrix

Extract the same checklist a test-writer should have extracted: every public
method/function/CLI surface in scope, each with:
- its happy path
- each documented error condition / exception
- each documented edge case or invariant

Then map every test function in the suite under review to the checklist item(s) it covers.
Produce a table:

| Behavior/contract (from design.md) | Covering test(s) | Status |
|---|---|---|

Status values:
- **Covered** — a test clearly exercises this and asserts on the right observable outcome.
- **Partially covered** — e.g. happy path tested, but a documented error case isn't.
- **Missing** — nothing tests it.
- **Untraceable** — a test exists that doesn't map to anything in design.md. This isn't
  automatically bad (it might be a reasonable internal edge case), but flag it — if it encodes a
  behavior detail (retry counts, timeouts, specific formats) that isn't actually written in
  design.md, that's a guessed assumption masquerading as spec, and should route back to design
  clarification the same way kiro-spec-tdd's ambiguity step would have caught it upstream.

## Step 2: Coverage report

Summarize from the matrix:
- All Missing rows, prioritized by how central the behavior is (public API entry points and
  documented exceptions first; obscure edge cases last).
- All Partially covered rows and specifically what's uncovered within them.
- All Untraceable tests and whether they look like reasonable extra edge-case coverage or a
  hidden undocumented assumption.

## Step 3: Correctness pass — per-test anti-pattern scan

For every test function, check for:

- **Asserting on internals** — reaching into `_private_attr`, `__mangled`, or otherwise
  inspecting implementation state instead of public output/exceptions/return values.
- **Over-mocking** — mocking the class-under-test's own collaborators so heavily that the
  assertion only proves "a mock was called" rather than "correct behavior occurred." For
  networked/IO-heavy code specifically: check mocks are at the true I/O boundary (transport,
  socket, client library) and not at the project's own wrapper classes (e.g. a test of
  `Parser` should not mock `Parser.parse()` itself, and a test of something that calls
  `Fetcher` should mock the transport `Fetcher` uses, not `Fetcher` itself, unless `Fetcher` is
  a genuinely external dependency to the unit under test).
- **Tautological / vacuous tests** — see Step 4, the delete-the-implementation check, for the
  reliable way to catch these.
- **Conditional logic in the test body** — `if`/`for`/`try` beyond a single `pytest.raises`
  signals multiple behaviors bundled into one test, hiding which one actually failed.
- **Unrelated assertions bundled together** — multiple `assert` calls are fine if they check
  facets of the same behavior; flag it if they're actually checking unrelated behaviors for
  convenience.
- **Vague names** — `test_fetch_2`, `test_it_works` instead of names describing behavior +
  condition (`test_fetch_raises_timeout_error_after_max_retries_exceeded`).
- **Snapshot tests for a simply-describable contract** — flag if a snapshot is used where a
  direct assertion on specific fields/values would be simpler and clearer; snapshots are
  appropriate for genuinely unstructured output only.

## Step 4: The delete-the-implementation sanity check

This is the single cheapest and most reliable check — do not skip it.

- **If implementation already exists**: in a scratch copy, stub out the function/method bodies
  in scope (`pass` / `return None` / `raise NotImplementedError`) and re-run the test file.
  Every test in scope should fail. Any test that still passes with no real implementation behind
  it is vacuous — it isn't testing anything, regardless of how it reads. Flag it explicitly and
  say why (usually: assertion is trivially true, or the test never actually calls the code path
  it claims to test).
- **If implementation doesn't exist yet** (pre-implementation review, right after kiro-spec-tdd's
  test-writing pass): confirm each test currently fails with an error tied to the missing
  behavior (`ImportError`, `AttributeError`, or a real assertion failure) — not something
  unrelated like a broken fixture or bad import path. If a test errors for the wrong reason,
  that's a broken test, not a legitimate red — flag it before it reaches the implementation
  pass, per kiro-spec-tdd's own Step 3, as a double-check that step was done correctly.

## Step 5: Produce the review report

Structure the output as:

1. **Traceability matrix** (Step 1)
2. **Coverage gaps**, prioritized (Step 2)
3. **Anti-pattern findings**, one entry per flagged test, naming the specific problem and a
   concrete suggested fix (not just "this test is bad")
4. **Vacuous/broken tests** found via the delete-the-implementation check (Step 4)
5. **Overall verdict** — one of:
   - *Ready for implementation handoff* — coverage is complete against design.md and no
     correctness problems found.
   - *Needs revision* — list the specific blocking items from 2-4 that must be resolved first.

## What NOT to do

- Don't approve a suite just because it's syntactically valid pytest and currently green/red as
  expected — passing or correctly-failing tells you nothing about coverage against the spec.
- Don't silently add tests to fill gaps you find — report the gap and hand it back to the
  test-writing pass (or the user); keep this skill's role as *review*, not *generation*, so
  there's always a visible, separate step where new tests are actually authored and can be
  checked in turn.
- Don't skip the delete-the-implementation check because it's tedious — it's the one technique
  that reliably catches a test that reads fine but proves nothing.
- Don't judge coverage against general domain conventions ("crawlers usually need X") when
  design.md doesn't actually say X — that's inventing spec, the same failure mode
  kiro-spec-tdd's generation pass is built to avoid. If you think design.md is missing something
  important, flag it as a spec gap, not a test gap.
