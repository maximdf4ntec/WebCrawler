<!-- Based on/adapted from:
     https://github.com/github/awesome-copilot/blob/main/skills/pytest-coverage/SKILL.md -->
---
name: pytest-coverage
description: Run pytest with coverage, find lines missing coverage, and close the gaps with real tests. Activates when the user asks to check/increase test coverage, or as a step within the code-reviewer skill's Testing check.
---

# Pytest Coverage Skill

Goal: every line of `trajectory_to_tests/` is covered by a test that verifies real behavior — not coverage for its own sake, but coverage as a forcing function to find untested branches.

## Steps
1. Run: `pytest --cov=trajectory_to_tests --cov-report=annotate:cov_annotate`
2. Open `cov_annotate/` — one annotated file per source file.
3. Skip any file at 100% (no `!`-prefixed lines).
4. For each file below 100%, open the matching annotated file. Lines starting with `!` are not covered by any test.
5. For each uncovered line/branch:
   - If it's reachable behavior: write a test that exercises it, following the `tdd` skill's rules (test through the public seam, descriptive name).
   - If it's genuinely unreachable (defensive code, `assert False` after an exhaustive match): mark with `# pragma: no cover` and a one-line comment explaining *why* it's unreachable — don't silently exclude it.
6. Re-run coverage to confirm the gap closed and nothing else regressed.
7. Delete `cov_annotate/` before committing (it's a working artifact, not repo content) — add it to `.gitignore` if not already there.

## When invoked from code-reviewer
Report gaps per-file in the review's Testing section rather than as a separate wall of output — the reviewer needs "this changed file has 2 uncovered branches" inline with the rest of the findings, not a disconnected coverage dump.
