---
name: code-reviewer
description: >
  Two-axis review (Standards + Spec) of implementation code for correctness, security,
  performance, and adherence to the design — scoped to a single currently-implemented task
  from a kiro spec's tasks.md, not a full-repo or full-feature review. Runs the locked test
  file already generated for that task's module (by kiro-spec-tdd) and reports any failures
  as findings, without editing the tests. Activates when the user provides a code block, a
  file path, a diff, or says "review this", "review since REF", "review this PR", or
  "review task N".
---

# Code Reviewer Skill

## Why two axes
Code can nail the spec while breaking your conventions, or follow every convention while
building the wrong thing. Reviewing both at once conflates the two kinds of failure and buries
findings. This skill runs them separately, then reports side by side. (Pattern:
mattpocock/skills `code-review`.)

- **Standards axis** — does the diff follow this repo's documented coding standards (below)
  plus a baseline of classic code smells (long functions, feature envy, primitive obsession,
  shotgun surgery)?
- **Spec axis** — does the diff faithfully implement what **this one task** asked for. Pass/fail
  against each requirement stated for that task specifically, not the whole feature.

If you have sub-agent / parallel-task capability, run these as two independent passes so one
doesn't contaminate the other's context. If not, do Standards first, Spec second, as two clearly
separated sections — don't interleave.

## Trigger
- User provides a code block, file path, or diff
- User says "review this", "review since <ref>", "review this PR", "review my implementation",
  or "review task N"
- Before merging any change that touches `trajectory_to_tests/` core modules

## Step 0: Resolve task scope — do this before anything else

This skill reviews **one task's worth of change**, not the whole diff/PR/repo. Before touching
either axis:

1. **Identify the task.** In priority order:
   - The user names it explicitly ("review task 4", "review the fetcher task").
   - It's inferable from the diff/PR itself (branch name, commit message referencing a task ID,
     or the fact that only one task's files were touched).
   - Otherwise, check `.kiro/specs/<feature>/tasks.md` for the task most recently marked
     in-progress or just-completed.
   - If still ambiguous, ask which task before proceeding — don't guess and review something
     broader "to be safe." Reviewing the wrong scope is worse than asking.

2. **Pull that task's exact requirements** from `tasks.md` — this list, and only this list,
   becomes the Spec axis checklist. Do not pull in requirements from other tasks in the same
   file, even if they're related or you can see they're also unfinished.

3. **Pull the relevant contracts from `design.md`** for whatever module(s) this task covers —
   signatures, exceptions, data shapes — the same source `kiro-spec-tdd` used to generate this
   task's tests. This is what the Standards axis's "design compliance" checks against, not the
   whole design document.

4. **Determine the file scope.** Based on the task description and design.md, decide which
   files this task should have touched. If the diff includes changes outside that scope
   (e.g. an unrelated module, a shared `conftest.py` edit that's plausibly needed, a stray
   formatting pass on an unrelated file), don't fold them into the review — call them out
   separately under a short "Out of scope for this task" note, and only review them in depth if
   the user asks.

Report the resolved scope explicitly at the top of your output (which task, which files) before
the axes — this makes it easy for the user to catch a wrong-scope resolution immediately rather
than discovering it three findings in.

## Before reviewing (within the resolved scope)
1. Resolve the fixed point: if reviewing a diff, confirm `git rev-parse <ref>` resolves and the
   diff isn't empty. Fail loudly here, not mid-review.
2. Confirm the task's spec and design sections were found in Step 0. If `tasks.md` or
   `design.md` couldn't be located at all, say so and stop rather than falling back to reviewing
   without a spec.
3. Locate this repo's standards: `CODING_STANDARDS.md`, `CONTRIBUTING.md`, `pyproject.toml`
   (ruff/mypy config), `.github/copilot-instructions.md`. Everything below is a floor, not a
   replacement for what's documented there.

## Standards Axis Checklist (within scope only)

### Correctness
- Does it run? Are imports correct, no circular imports between `parsers/`, `rules/`,
  `exporters/`?
- Are error paths tested, not just the happy path?

### Python-specific
- Type hints on every public function signature (this is a library — untyped public APIs are a
  bug, not a style nit). `py.typed` marker present if not already.
- No mutable default arguments (`def f(x, cache={})`).
- No bare `except:` — catch specific exceptions; if catching broadly at a boundary, re-raise
  with context (`raise X from e`).
- `pathlib.Path` over string path concatenation.
- Docstrings (Google or NumPy style — match whatever's already in the repo) on all public
  classes/functions, including the trajectory schema fields they touch.
- Logging via `logging` module, not `print`, in library code (prints are fine only in CLI entry
  points).

### Security (non-negotiable, flag immediately)
- **YAML**: any `yaml.load(...)` without `Loader=yaml.SafeLoader` (or plain `yaml.safe_load`) is
  arbitrary-code-execution risk — this matters directly for you since DeepEval YAML
  export/import is core functionality. Flag every instance.
- No hardcoded API keys/tokens (OpenAI, Langfuse, etc.) — must come from env vars or a config
  object.
- No `eval`/`exec` on trajectory content parsed from untrusted input (agent trajectories may
  originate from third-party logs).
- Path traversal: any file write derived from trajectory/test-name strings must be sanitized
  before touching disk.

### Design compliance (repo-specific, this task's contracts only)
- Does the implementation match the exact signature/exception/data-shape design.md specifies
  for this task's module — not a plausible-looking alternative?
- Tool-provenance anchoring: if code disambiguates "was this text generated by the tool or by
  LLM response noise," verify it doesn't rely on brittle string matching alone — check the
  existing anchoring strategy is followed consistently.
- Hard rules (pytest assertions) vs soft rules (DeepEval YAML) stay in their respective code
  paths — a change that starts blurring which rule type owns which behavior is a design smell,
  flag it even if each half works.

### Classic smells (Fowler baseline)
- Function > ~40 lines or doing more than one thing → flag for extraction.
- Duplicated logic across `parsers/` for different trajectory formats (OpenAI Messages vs
  others) → flag for shared abstraction, but don't force one prematurely if the formats
  genuinely diverge.
- Primitive obsession: raw dicts passed around where a small dataclass/TypedDict would make the
  shape self-documenting.

## Spec Axis Checklist
- Enumerate every requirement from **this task's entry in tasks.md** as a checklist — not the
  whole feature's tasks.md.
- Mark each Pass/Fail against the actual diff — not "looks like it probably handles X."
- Explicitly call out anything implemented that *wasn't* asked for in this task (scope creep is
  a finding, not a bonus) — including work that belongs to a *different* task in the same file.

## Test Execution (run the locked tests, don't edit them)

The test file for this task's module was already generated by `kiro-spec-tdd` before this
implementation pass — treat it as fixed spec, not something this skill authors or fixes.

1. **Locate the test file** using the same module-path convention `kiro-spec-tdd` used (e.g.
   `crawler/fetcher.py` → `tests/test_fetcher.py` or `tests/fetcher/test_*.py` — match whatever
   the project already established).
2. **Run only that file**, not the whole suite — keep failure attribution scoped to this task:
   ```
   pytest tests/path/to/test_module.py -v
   ```
3. **Report every result**, not just a pass/fail count — list which test functions passed and
   which failed, with the actual assertion/exception diff for failures.
4. **Any failing test is a Critical finding on the Standards axis** — "implementation does not
   satisfy locked test `test_name`," with the failure detail. This is the review's primary
   correctness signal, since the test already encodes the spec's contract.
5. **If a test looks wrong, missing, or like it's testing an assumption not in design.md** —
   don't fix, add, or edit it here. Note it explicitly as a separate finding ("possible test
   issue, not an implementation issue") and say it should go back through `kiro-test-review` or
   `kiro-spec-tdd`, not be patched silently in this pass.
6. **Coverage, scoped to this task's files only** (not the whole repo):
   ```
   pytest tests/path/to/test_module.py --cov=<module> --cov-report=annotate:cov_annotate
   ```
   Check `cov_annotate/` for any `!`-marked (uncovered) lines in the files this task touched.
   Report gaps as findings — don't add tests to close them; that's `kiro-test-review`'s job.
7. **Mocking check while you're in the test file anyway**: confirm external calls (LLM API
   calls, Langfuse client, filesystem writes) are mocked at the true boundary, not at the
   project's own wrapper classes — same standard as `kiro-spec-tdd`'s
   `references/mocking.md`. This isn't re-litigating test correctness (that's
   `kiro-test-review`'s job) — just flag it here if something would make the test run flaky or
   hit real network/disk, since that's a review-blocking issue either way.

## Output Format

```markdown
# Code Review: [Task N — short name]
**Scope:** tasks.md item #N ("...") — files: `crawler/fetcher.py`, `tests/test_fetcher.py`
**Out of scope (not reviewed in depth):** `crawler/scheduler.py` (touched only for an import fix)
**Rating:** ⭐️⭐️⭐️ (3/5) — Changes Requested

## Axis 1 — Standards
### 🐛 Critical
1. `parsers/openai.py:45` — `yaml.load()` without SafeLoader. Arbitrary code execution risk on
   untrusted trajectory input.
2. Test failure: `test_fetch_raises_timeout_error_after_max_retries_exceeded` — expected
   `FetchTimeoutError`, implementation raises bare `TimeoutError`. Implementation doesn't match
   the locked test's contract.
### 🧹 Refactoring
- `rules/soft.py::process_data` is 62 lines and does parsing + validation + export. Split into
  three functions.
### 🔒 Security
- (as above, or "none found")

## Axis 2 — Spec (Task N only)
Source: `.kiro/specs/web-crawler/tasks.md`, item #N
- [x] Implement `Fetcher.fetch()` with retry/backoff per design.md
- [ ] Raise `FetchTimeoutError` after max retries — **not implemented**, see test failure above

## ✅ Test Execution
Ran `tests/test_fetcher.py` (locked, not modified):
- 6 passed, 1 failed (see Critical #2 above)
- Coverage: `crawler/fetcher.py` at 91%; one uncovered branch at line 78 (backoff jitter) — flagged, not fixed
- Possible test issue: `test_fetch_retries_on_connection_reset` mocks `Fetcher.fetch` itself
  rather than the transport — route to `kiro-test-review` to confirm this is intentional.
```
