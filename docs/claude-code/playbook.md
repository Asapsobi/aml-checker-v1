# Building amlcheck with Claude Code — playbook

> How the owner and Claude Code work together on this repo. Short version: **one phase per branch, one
> ticket per session, tests before code, verify before trusting a provider, review before merge.**

---

## 1. What's in the kit

| Piece | Where | Use |
|---|---|---|
| Project memory | `CLAUDE.md` | Loaded every session: constraints, doc map, commands, non-negotiables, conventions |
| Specs | `docs/00`–`10` | The source of truth. Code follows docs; when they disagree, ask |
| Permissions | `.claude/settings.json` | Pre-approves tests, lint, type checks and local git; blocks reading `.env` |
| `/start-phase N` | `.claude/skills/start-phase/` | Branch, load the phase scope, verification first, plan, wait for go-ahead |
| `/next-ticket [ID]` | `.claude/skills/next-ticket/` | Pick the next ticket, tests first, implement, check, commit |
| `/verify-source VS-NN` | `.claude/skills/verify-source/` | Live-check a provider fact, save fixtures, log it |
| `/record-decision "title"` | `.claude/skills/record-decision/` | Add or supersede a decision |
| `/ask-owner "question"` | `.claude/skills/ask-owner/` | Log a question instead of guessing |
| `/phase-review` | `.claude/skills/phase-review/` | Pre-PR checklist, version bump, PR description draft |
| `spec-reviewer` | `.claude/agents/` | Read-only review of a diff against the specs |
| `test-author` | `.claude/agents/` | Writes tests from AT rows and thresholds |
| `source-verifier` | `.claude/agents/` | Live provider checks with quota and secret discipline |
| Phase prompts | [phase-prompts.md](phase-prompts.md) | Copy-paste openers per phase |

`/start-phase` and `/phase-review` only run when you type them. The others Claude can also pick up on
its own when the situation matches their description.

---

## 2. One-time setup (owner)

1. Clone the repo and open Claude Code in it.
2. Create `.env` from `.env.example` once P0 has written it. Keys needed: TronGrid API key, Envio
   HyperSync token; later an API token for the local API. No third-party AML API keys (D-033). Claude Code never reads
   this file (blocked in settings); code reads keys from the environment.
3. Answer the open questions marked "Needed by P0–P2" in `docs/10-open-questions.md` (Q-01, Q-02,
   Q-03), or accept the proposed answers.

---

## 3. The phase loop

```
/start-phase N ──► review the plan, answer blocking questions, say "go"
      │
      ▼
/next-ticket ──► (repeat per ticket; /clear between tickets)
      │
      ▼
/phase-review ──► read the PR draft, try the 2–3 commands it lists
      │
      ▼
open the PR ──► you review on GitHub ──► merge ──► tag v0.N.0 ──► next phase
```

| Step | Owner does | Claude Code does |
|---|---|---|
| Start | Types `/start-phase N`, reads the plan, answers questions | Branches, loads only this phase's scope, lists verification tickets first |
| Tickets | Watches or steps away; answers questions as they come | Tests → code → checks → one commit per ticket |
| Live checks | Provides keys in `.env`; may need to run a command with real data | Runs live checks in a scratch `AMLCHECK_HOME`, records results |
| Review | Reads the PR, tries the commands, merges | Drafts the PR with questions first and decisions listed |
| Release | Tags (or asks Claude Code to after merge) | Bumps version before the PR |

---

## 4. Working well with Claude Code here

| Practice | Why |
|---|---|
| **One ticket per session.** `/clear` after each commit | Keeps context small and focused on the ticket's doc sections |
| **Plan first for L tickets.** Switch to plan mode (Shift+Tab) and ask for the plan before edits | The trace engine and adapters are easy to get subtly wrong |
| **Point at the spec section.** "Implement methodology §7.4" beats "implement pruning" | The docs are exact; the model shouldn't paraphrase them |
| **Verify before adapters.** Never let an adapter be written from memory | Provider APIs changed twice in the research for this project |
| **Tests first, at the boundary.** Every threshold tested at and just past its value | Most AML logic bugs are `>` vs `≥` |
| **Run `spec-reviewer` on risky diffs**, not just at the end | Rules, trace, score and audit changes deserve a second look |
| **Questions are cheap.** Prefer `/ask-owner` to a guess | A wrong default in AML is worse than a day's delay |
| **Keep `CLAUDE.md` short.** Add a line only when the same correction is needed twice | It's loaded every session |
| **Parallel work only on independent phases.** E.g. P8 web pages while P6 trace is built, each in its own git worktree and branch | Avoids two sessions editing the same modules |

---

## 5. When things go wrong

| Symptom | Fix |
|---|---|
| Code uses an API field the docs don't mention | Stop. `/verify-source` for that provider; add the fixture; then fix |
| A check shows `NO_HITS` with a source in error | Non-negotiable #1 broken. Ask `spec-reviewer` to find every path to `NO_HITS` |
| `audit verify` fails on an old DB after a migration | A hashed field was added unconditionally. Hash it only when set |
| Trace numbers don't sum to 1 | Run the property tests (AT-38); look for weight assigned twice or dropped on cycle/budget |
| Claude Code drifts into a later phase's feature | Point to the roadmap phase block and `/clear` |
| Free-tier limits hit during a trace | Expected sometimes. Check pacer logs; if routine, raise Q-09 with measurements |
| Docs contradict each other | `/ask-owner`; once answered, `/record-decision` and fix the docs in the same PR |

---

## 6. Owner's checklist per PR (10 minutes)

- [ ] Open questions at the top answered or accepted
- [ ] Decisions listed look right
- [ ] Acceptance tests named for every AT-ID of the phase
- [ ] Live results look plausible (addresses, verdicts, timings)
- [ ] Tried the 2–3 commands in the description
- [ ] Nothing reads, prints or commits a key
