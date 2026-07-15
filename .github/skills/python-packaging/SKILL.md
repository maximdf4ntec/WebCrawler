<!-- Based on/adapted from:
     https://github.com/github/awesome-copilot/blob/main/skills/python-pypi-package-builder/SKILL.md
     That skill is large and battle-tested end-to-end (all 4 build backends, PEP 440, trusted publishing).
     Rather than re-deriving it, this file records the decisions it recommends AS APPLIED to
     trajectory-to-tests, plus a pointer to install the upstream skill verbatim for anything not covered here.
     Install upstream: gh skill install github/awesome-copilot python-pypi-package-builder -->
---
name: python-packaging
description: Packaging, typing, versioning, linting, and PyPI publishing standards for trajectory-to-tests. Activates when creating/editing pyproject.toml, adding a dependency, setting up CI/CD, or preparing a release.
---

# Python Packaging Skill

This project is a public, pip-installable library — every packaging decision below optimizes for "someone else installs this and gets a good `import trajectory_to_tests` experience with working types and no surprises."

## Decisions locked in for this project
(If any of these is wrong for where the project actually is, say so and this file gets updated — don't silently deviate.)

- **Build backend:** `hatchling`. Simpler `pyproject.toml` than setuptools, no `poetry.lock` ecosystem lock-in, works cleanly with `hatch-vcs` for git-tag-based versioning.
- **Versioning:** `hatch-vcs` — version derived from git tags (PEP 440 compliant, e.g. `0.3.1`, `0.4.0.dev3+g1a2b3c4` between tags). No manually-edited `__version__` string to forget to bump.
- **Typing:** ship `py.typed` (PEP 561) at the package root — this is a library other code will type-check against; untyped-but-claims-typed is worse than honestly untyped. `mypy --strict` in CI on the `trajectory_to_tests/` package (test files can be less strict).
- **Lint/format:** `ruff` for both linting and formatting — replaces black+isort+flake8 with one tool and one config block in `pyproject.toml`.
- **Dependency groups:** runtime deps minimal and pinned by range, not exact version. `langfuse` and `deepeval` integrations are optional extras (`pip install trajectory-to-tests[langfuse]`) — don't force every user of the core pytest-assertion feature to pull in Langfuse's dependency tree.
- **Publishing:** GitHub Actions + PyPI Trusted Publishing (OIDC) — no long-lived PyPI token sitting in repo secrets.

## Checklist for any pyproject.toml / CI change
- [ ] `[build-system]` still points at `hatchling` unless there's a documented reason to switch
- [ ] `py.typed` file exists and is included in the built wheel (check `[tool.hatch.build]` includes it)
- [ ] Optional integrations (`langfuse`, `deepeval`) declared under `[project.optional-dependencies]`, not `[project.dependencies]`
- [ ] `ruff check` and `mypy --strict` both run in CI before tests, not after — fail fast on style/type issues before spending CI minutes on the test suite
- [ ] Release workflow triggers on git tag push, uses OIDC trusted publishing, not a stored `PYPI_API_TOKEN`
- [ ] `CHANGELOG.md` updated for any user-facing change (Keep a Changelog format) — this is the artifact external users actually read before upgrading

## For anything not covered above
The upstream `python-pypi-package-builder` skill is comprehensive (all four backends, full PEP 440 semver decision trees, migration guides). Install it directly rather than this project re-deriving it:
`gh skill install github/awesome-copilot python-pypi-package-builder`
