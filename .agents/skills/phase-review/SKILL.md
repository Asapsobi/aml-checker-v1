---
name: phase-review
description: Final review before opening a phase PR for amlcheck - runs all checks, confirms exit criteria and acceptance tests, updates docs, bumps the version and drafts the PR description. Use when all tickets of a phase are done.
disable-model-invocation: true
---

# Phase review

## Current state

- Branch: !`git branch --show-current`
- Commits in this phase: !`git log --oneline main..HEAD`
- Working tree: !`git status --short`

## Checklist

1. **Tickets.** Every ticket of the phase in `docs/08-backlog.md` has a commit, or an agreed question
   explains why not.
2. **Checks.** `uv run pytest -q --cov`, `uv run ruff check .`, `uv run ruff format --check .`,
   `uv run mypy` all green.
3. **Acceptance.** Every AT-ID of the phase (`docs/06-acceptance-tests.md`) maps to named tests.
   Update `docs/acceptance-results.md` with that mapping and any live results (date, address
   shortened, result, timing).
4. **Spec conformance.** Run the `spec-reviewer` subagent on `git diff main...HEAD`. Fix every
   *must-fix*; list the rest in the PR.
5. **Compatibility.** Migrations apply to the previous release's DB; `audit verify` passes on it.
6. **Docs.** README (new commands), `config.example.toml` (new keys), `docs/verification-log.md`,
   `docs/09-decisions.md`, `docs/10-open-questions.md` are current.
7. **Version.** Bump `version` in `pyproject.toml` to the phase's release (`docs/07-roadmap.md`),
   run `uv lock`, commit `P<N>: release v0.N.0`.
8. **PR description** (draft it, don't open the PR unless asked):
   - Open questions first
   - What the phase delivers (2–4 lines)
   - Decisions taken (IDs)
   - Acceptance tests and live results
   - Spec-review notes not fixed
   - How to try it (2–3 commands)
