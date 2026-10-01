---
name: start-phase
description: Start a build phase of amlcheck (P0–P11) - creates the branch, loads the phase's scope, verification items and tickets, and proposes a plan. Use when the owner says to begin a phase.
argument-hint: "[phase number, e.g. 3]"
disable-model-invocation: true
---

# Start phase P$ARGUMENTS

## Current state

- Branch: !`git branch --show-current`
- Recent commits: !`git log --oneline -8`
- Working tree: !`git status --short`

## Steps

1. **Check readiness.** Read `docs/07-roadmap.md`. Confirm the previous phase's exit criteria are met
   (its tag exists: run `git tag --list 'v*'`). If not, stop and say what is missing.
2. **Branch.** Create `p$ARGUMENTS-<slug>` from an up-to-date `main` (slug from the phase name, e.g.
   `p3-exposure`). Don't switch if the working tree is dirty; ask instead.
3. **Load scope** (only these sections):
   - The phase block in `docs/07-roadmap.md`
   - The phase's tickets in `docs/08-backlog.md`
   - The PRD functional requirements tagged with this phase in `docs/01-prd.md` §8
   - The methodology sections those requirements cite
   - The phase's acceptance tests in `docs/06-acceptance-tests.md`
   - Any `VS-` items for this phase in `docs/04-data-sources.md` §9
   - Open questions in `docs/10-open-questions.md` marked "Needed by" this phase
4. **Verification first.** If the phase has `VS-` items that are not yet in
   `docs/verification-log.md`, they are the first tickets. Use the `source-verifier` subagent.
5. **Questions.** List every open question this phase needs, with the proposed answer. Say which
   tickets each one blocks; the rest can proceed.
6. **Plan.** Present: tickets in order, which AT-IDs each satisfies, risks, and anything in the docs
   that looks inconsistent. Then wait for the owner's go-ahead before writing code.
