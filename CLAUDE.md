# CLAUDE.md

Project memory for Claude Code. Keep it short; details live in `docs/`.

## What this is

**amlcheck**: a local counterparty-intelligence tool for USDT on TRON (TRC20) and BNB Smart Chain
(BEP20). It screens addresses (sanctions, issuer freezes, exposure, behaviour), traces source of funds
up to 3 hops, classifies unknown addresses, scores risk, and records operator decisions.
Built **from scratch** here, phase by phase (P0–P15).

**Hard constraints:** no own nodes (RPC providers / indexers only) · no third-party AML APIs · intelligence only on our
counterparties and what their traces reach · local-first, SQLite · internal use only.

## Where things are

| Need | File |
|---|---|
| What to build | `docs/01-prd.md` |
| Exact rules, algorithms, thresholds, formulas | `docs/02-methodology.md` |
| Stack, package layout, interfaces | `docs/03-architecture.md` |
| Provider facts + what to verify | `docs/04-data-sources.md` |
| Schema / migrations | `docs/05-data-model.md` |
| Definition of done | `docs/06-acceptance-tests.md` |
| Phases / tickets | `docs/07-roadmap.md`, `docs/08-backlog.md` |
| Decisions / questions | `docs/09-decisions.md`, `docs/10-open-questions.md` |
| How we work | `docs/claude-code/playbook.md` |

Read only the sections a ticket needs; don't load every doc every session.

## Commands (from P0 on)

```bash
uv sync
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run mypy
AMLCHECK_HOME=$(mktemp -d) uv run amlcheck check <address>   # live run, never touches real data
```

## Non-negotiables

1. **Never a clean result over a gap.** A required source that errored or is stale ⇒ `INCOMPLETE`.
2. **Audit before output.** Every check is appended to the hash-chained log before it is shown.
   Released records must keep verifying (new nullable fields are hashed only when set).
3. **Verify external facts before coding against them.** Never invent API fields. Record real answers
   as fixtures in `tests/fixtures/`; log the check in `docs/verification-log.md`.
4. **Tests never touch the network.** respx + fixtures only.
5. **Inferred ≠ fact.** Classifications never produce BLOCK; always show confidence.
6. **Deterministic.** Same cache + config ⇒ same output. Sort everything with a tie-break; time only
   from the injected clock.
7. **Sources and licences.** Only public lists and RPC providers / indexers: no third-party AML,
   screening or freeze API (D-033). No label data without a recorded licence.
8. **No secrets in the repo.** Keys only in `.env` (never read it; use `.env.example`).
9. **Ambiguity ⇒ ask.** Add to `docs/10-open-questions.md` with a proposed answer, keep building what
   isn't blocked. Real choices ⇒ add a decision to `docs/09-decisions.md`.

## Conventions

- Python 3.12+, `mypy --strict` everywhere, ruff (rules in `pyproject.toml`), line length 100.
- Amounts are `Decimal`, serialised as strings. Times are UTC `datetime`, ISO-8601 in storage.
- Domain objects are frozen dataclasses; Pydantic only for config and I/O schemas.
- Every source is a `SourceAdapter`; failures become `failed()` results, never exceptions out of the engine.
- Findings carry rule ID, severity, source, plain-English summary, evidence, `observed_at`.
  Low priority is `"priority": "low"` in evidence, not a new severity.
- Migrations: numbered SQL files, append-only once released.
- Comments and docstrings explain *why*, citing doc sections (e.g. "methodology §7.4").
- Docs: plain English, short sentences, tables first.

## Workflow

- One phase = branch `p<N>-<slug>` → PR → owner review → merge → tag `v0.N.0` (P11 → `v1.0.0`;
  P12–P15 → `v2.0.0a1`, `a2`, `b1`, `v2.0.0`).
- Never push code to `main` directly. Never tag before the owner merges.
- Commit per ticket: `T-<phase>.<nn>: <what>`.
- Skills: `/start-phase N`, `/next-ticket`, `/verify-source VS-NN`, `/record-decision`, `/ask-owner`,
  `/phase-review`. Subagents: `spec-reviewer`, `test-author`, `source-verifier`.
