<!-- Based on/adapted from:
     https://github.com/mattpocock/skills/blob/main/skills/engineering/codebase-design/SKILL.md (deep-module design discipline)
     Front-loads the checks already defined in code-reviewer, tdd, python-packaging, and domain-modeling,
     so they're applied while code is generated rather than only caught afterward. -->
---
name: code-writer
description: Standards to apply while writing or generating new implementation code — not for reviewing existing code. Activates whenever the user asks to implement a feature, add a function, fix a bug, or otherwise write new Python code for trajectory-to-tests.
---

# Code Writer Skill

`code-reviewer` catches problems after they exist. This skill's job is to make fewer of them exist in the first place — the same standards, applied at generation time. If you're about to write implementation code, read this first; if you're reviewing code someone/something else wrote, use `code-reviewer` instead.

## Before writing a line of code
1. **Is there a test yet?** If not, stop and use the `tdd` skill — write the failing test for this one slice of behavior first. Do not write implementation code with no corresponding test in the same change.
2. **Does the vocabulary exist?** Check `GLOSSARY.md` (see `domain-modeling` skill). If the feature introduces a concept with no existing term (a new rule type, a new trajectory format), name it and add the glossary entry *before* writing code that uses it — don't invent ad-hoc names and reconcile later.
3. **Where does this belong?** New logic goes in the module matching its actual responsibility (`parsers/`, `rules/`, `exporters/`, `langfuse_integration/`) — don't bolt a new concern onto whatever file happens to be open.

## Design discipline: deep modules, not wide ones
Favor modules with a small public interface hiding real work behind it, over modules that expose lots of small functions the caller has to sequence themselves. Concretely:
- If a caller has to call three of your functions in a specific order to get a correct result, that's a shallow module — collapse it into one function/method that does the sequencing internally.
- A new public function should be justifiable as "the smallest surface that lets a caller get [specific behavior] without knowing how it works internally" — not "the pieces I happened to build."
- Find the seam (the public boundary something will be tested through, per the `tdd` skill) *before* writing the implementation behind it, not after.

## Write it typed, documented, and safe the first time
(Same bar as `code-reviewer` checks for — meeting it here means the review is confirming, not fixing.)
- Type hints on every public function signature, from the first draft.
- Docstring on every public class/function stating what it does and, for anything touching the trajectory schema, which fields it reads/writes.
- `pathlib.Path`, not string path concatenation.
- Specific exception types, never bare `except:`; re-raise with context (`raise X from e`) at any boundary that catches broadly.
- `logging`, never `print`, outside CLI entry points.
- **Security, non-negotiable at write time**: `yaml.safe_load` — never plain `yaml.load` — for any YAML touching trajectory or rule content. No `eval`/`exec` on parsed trajectory content. Sanitize any path built from trajectory/test-name strings before it touches disk. No hardcoded API keys — pull from env/config.
- No mutable default arguments.

## Match the project's packaging reality
Public API surface (anything importable from `trajectory_to_tests`) needs type hints strict enough to satisfy `mypy --strict` per the `python-packaging` skill — don't write something that passes locally but fails CI type-checking; run `mypy --strict` on the module yourself before considering the slice done.

## Definition of done for a slice (self-check before handing off)
Before treating a vertical slice as finished, confirm — don't wait for `code-reviewer` to tell you:
- [ ] Test exists and was red before the implementation, green after
- [ ] Public function(s) typed, docstringed, named consistently with `GLOSSARY.md`
- [ ] No bare except, no mutable defaults, no `print`, no unsafe YAML load, no hardcoded secrets
- [ ] External calls (LLM, Langfuse, filesystem) go through the same seams the `tdd` skill's `mocking.md` expects — don't invent a new integration pattern one-off
- [ ] `ruff check` and `mypy --strict` pass locally on the changed files

This checklist overlaps with `code-reviewer` on purpose — the review is a second, independent pass (and the safety net if a hook enforces it automatically), not the first time these questions get asked.
