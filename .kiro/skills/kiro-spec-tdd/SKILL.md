---
name: kiro-spec-tdd
description: >
  Spec-first TDD for kiro-generated designs: generates the complete pytest test suite for one
  module/class at a time from .kiro/specs/FEATURE_NAME/design.md and tasks.md, *before* any
  implementation is written. A separate implementation pass (driven by tasks.md, possibly by
  another skill or by full-feature code generation) then makes those tests pass; a verification
  pass runs them and reports failures back for another round. Activates when the user says
  "let's TDD this", references a kiro spec/design.md/tasks.md, asks to generate tests for a
  module ahead of implementation, or asks to verify generated code against existing tests.
  Use this instead of writing implementation-then-tests, and instead of generating tests and
  code in the same pass.
---

# Kiro Spec TDD Skill

This is a **batch-mode** adaptation of red-green-refactor for pipelines where an AI generates
whole modules or whole tasks at once, driven by a spec. It intentionally does NOT do
one-assertion-at-a-time TDD. It preserves what makes TDD work — tests written and locked before
code exists, confirmed to fail for the right reason, then a real implementation pass, then
verification, then loop on failures — but at the granularity of one module or one task-file
entry, not one assertion.

There are three distinct passes. Each is a separate step; **do not merge the test-writing pass
with the implementation pass**, even if the same conversation is generating both. If you find
yourself about to write implementation code while also writing or editing a test in the same
step, stop — that's the exact collapse this skill exists to prevent.

1. **Test-writing pass** (this skill's main job) — read the spec, generate the full test file(s)
   for one module, confirm they fail correctly, then stop.
2. **Implementation pass** (tasks.md-driven, may be a different skill or a plain code-gen step)
   — given the locked tests and tasks.md, implement the module.
3. **Verification pass** — run the tests, report pass/fail, feed failures back to step 2 if any
   fail. Repeat step 2/3 until green, then move to the next module.

## Step 0: Locate and read the spec

Find the feature's spec folder: `.kiro/specs/FEATURE_NAME/`. You need both files:

- `design.md` — the source of truth for behavior, interfaces, and contracts.
- `tasks.md` — the source of truth for what's implemented in what order, used by the
  implementation pass to scope each unit of work. Read it during test-writing too, so your test
  files are scoped the same way tasks.md breaks up the work (one test file per task/module,
  not one test file for the whole feature).

If either file is missing, say so explicitly and ask where they are — don't infer a design from
the codebase or from general crawler/whatever-domain conventions instead.

## Step 1: Extract seams and contracts — flag ambiguity, don't guess

Before writing a single test, go through design.md and for each class/function/CLI surface the
current module touches, extract:

- Public method/function signatures: name, parameters (with types), return type.
- Exceptions raised and the conditions that trigger each one.
- Preconditions/postconditions and any explicitly stated invariants.
- Data shapes (schemas, dataclasses, TypedDicts) it consumes or returns.

**If design.md describes behavior but not a concrete signature or contract** (e.g., it says "the
fetcher retries on failure" but doesn't say how many times, with what backoff, or what exception
type surfaces after retries are exhausted), this is an ambiguity — not a gap to fill in with a
reasonable-sounding default. Do NOT invent the missing detail. Instead:

- Stop test generation for that specific behavior.
- Produce a short, explicit list of open questions, e.g.:
  - `Fetcher.fetch() retry behavior: design.md doesn't specify max retry count, backoff strategy, or the exception type raised after retries are exhausted. Need a decision before I can write a deterministic test.`
- Ask the user to resolve them, or point to the exact spot in design.md that needs updating.
- Continue generating tests for the parts of the module that ARE fully specified, so one
  ambiguity doesn't block the whole module.

This flagging step is the main difference from freeform test generation: never let the test
writer silently decide "well, probably 3 retries with exponential backoff" and encode that
guess as if it were the spec. A test built on a guessed contract will pass against an
implementation that guessed differently, or fail against a correct one — either way it's not
testing the spec, it's testing your assumption.

## Step 2: Generate the test file(s) for one module

Scope: one module/class per test-writing pass, matching how tasks.md scopes implementation
work. Mirror the module path (`crawler/fetcher.py` → `tests/test_fetcher.py` or
`tests/fetcher/test_*.py`, whichever the project's existing convention is).

Within that scope, write a full test file per the batch-mode process:

- One test function per distinct behavior/contract extracted in Step 1 (a happy path, each
  documented error condition, each documented edge case). Not one test per method — one test
  per *behavior*.
- Test names describe behavior + condition: `test_fetch_raises_timeout_error_after_max_retries_exceeded`,
  not `test_fetch_2`.
- Assert on the public seam only — inputs and observable outputs/exceptions/state, never
  internal attributes (`obj._cache`) or call-count-only mocks. See `references/tests.md` for
  worked examples and `references/mocking.md` for what's safe to mock in an HTTP-fetching
  codebase specifically.
- No conditional logic in a test body beyond a single `pytest.raises`.
- Shared setup goes in `conftest.py` at the narrowest scope that covers it; response/HTML
  fixtures (malformed HTML, redirects, timeouts, robots.txt variants) go in fixture files or
  fixtures, not inlined ad hoc per test.
- Every signature/exception/data-shape you assert on must trace back to something explicit in
  design.md. If you notice mid-generation that you're filling in a detail design.md didn't
  specify, stop and flag it per Step 1 rather than pushing forward.

## Step 3: Confirm red for the right reason — before handing off

This is the step batch mode is most likely to skip, and it's the cheapest insurance available.
Before declaring the test file done:

- Run it against the current (nonexistent-or-stub) implementation.
- Confirm every test fails with `ImportError`/`AttributeError`/a real assertion failure — not a
  broken test (typo, bad fixture, tautological assertion, wrong import path).
- If a test errors for a reason unrelated to the missing implementation (e.g., a fixture itself
  is broken), fix the test now, before handoff — not later, mixed in with real implementation
  fixes.

Report the result plainly: which tests exist, that they fail for the expected reason, and any
open questions from Step 1 that are still unresolved.

## Step 4: Hand off to implementation — do not touch the tests again

Once tests are confirmed red-for-the-right-reason, they're locked. State this explicitly when
handing off: "tests for `<module>` are locked; the following behaviors are still ambiguous and
excluded: [...]." The implementation pass (tasks.md-driven) must treat these tests as the fixed
spec, not something it can edit to make its own implementation choices pass.

If the implementer needs a test changed, that's a signal the test encodes an assumption not
actually in design.md — route it back through Step 1 (resolve the ambiguity, update design.md
or get an explicit user decision) rather than letting the implementation pass quietly rewrite
the test.

## Step 5: Verification pass and the loop

After implementation, run the module's test file:

- All green → this module is done; move to the next module/task in tasks.md.
- Some red → report which tests failed and why (assertion diff, exception mismatch, etc.), feed
  that back to the implementation pass for another attempt. Do not re-run the whole feature's
  test suite as the unit of iteration — keep the loop scoped to the module that's still red, so
  failures stay attributable.

## What NOT to do

- Don't generate tests for the entire feature/spec in one pass and then implement the entire
  feature in one pass — you lose the ability to tell which module's implementation caused which
  failure. Batch at module/task granularity, not feature granularity.
- Don't let the implementation pass edit test files to get to green.
- Don't fill a spec gap with a "reasonable" default silently — always surface it per Step 1.
- Don't skip Step 3 because "it's just AI-generated code, it's probably fine" — this is the one
  check that catches a broken test before it wastes a whole implementation-and-debug cycle.
