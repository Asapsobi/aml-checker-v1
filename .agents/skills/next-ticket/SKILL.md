---
name: next-ticket
description: Pick and implement the next ticket of the current amlcheck phase from docs/08-backlog.md - tests first, then code, then checks, then one commit. Use when continuing work within a phase.
argument-hint: "[optional ticket ID, e.g. T-3.04]"
---

# Next ticket

## Current state

- Branch: !`git branch --show-current`
- Commits on this branch: !`git log --oneline main..HEAD`
- Working tree: !`git status --short`

## Steps

1. **Pick.** If an ID was given (`$ARGUMENTS`), take it. Otherwise find the current phase from the
   branch name (`p<N>-…`), read that phase's table in `docs/08-backlog.md`, and take the first ticket
   whose ID does not appear in the commits above and whose **Needs** are all done.
2. **Scope.** Read only what the ticket needs: the PRD requirement(s), the methodology section, the
   architecture interface, the data-model table, the AT-IDs in **Done when**. State the ticket, its
   inputs and its done-condition in three lines.
3. **Unknowns.** If the ticket depends on an unverified external fact, stop and run
   `/verify-source` first. If the docs are ambiguous, `/ask-owner` and either pick the proposed
   answer (if reversible) or move to another ticket.
4. **Tests first.** Write or extend tests for the done-condition (use the `test-author` subagent for
   AT-based tests). They should fail for the right reason.
5. **Implement** the smallest change that passes. Follow `AGENTS.md` conventions. No speculative
   features from later phases.
6. **Check:** `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .`,
   `uv run mypy`. Fix everything before moving on.
7. **Commit:** `T-<phase>.<nn>: <what>`; mention AT-IDs covered in the body.
8. **Report** in a few lines: what changed, tests added, anything deferred, the next ticket.
