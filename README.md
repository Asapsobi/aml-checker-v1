# amlcheck

**Counterparty intelligence for USDT on TRON and BNB Smart Chain**: screening, source-of-funds tracing,
address classification, risk scoring and a recorded decision trail. Local-first, built on RPC
providers and indexers, no own nodes.

> **Status:** P0 (foundations) ready for review. The product is built from these docs with Claude Code,
> phase by phase.

## Setup

Needs [uv](https://docs.astral.sh/uv/) and Python 3.12+ (uv installs it if missing).

```bash
uv sync                      # install into .venv
uv run amlcheck --version
uv run amlcheck status       # creates ~/.amlcheck/ and the database
```

| What | Where | Override |
|---|---|---|
| Data, database, logs | `~/.amlcheck/` | `AMLCHECK_HOME` |
| Config | `~/.amlcheck/config.toml` (optional: every key has a default) | `AMLCHECK_CONFIG` |
| Keys | environment, then `./.env`, then `~/.amlcheck/.env` | — |

1. Copy [`.env.example`](.env.example) to `.env` and fill in the keys. Never commit it.
2. Optionally copy [`config.example.toml`](config.example.toml) to `~/.amlcheck/config.toml` and keep
   only the keys you change. Unknown keys and invalid values are refused, so a typo can't silently
   fall back to a default.
3. Try it without touching real data: `AMLCHECK_HOME=$(mktemp -d) uv run amlcheck status`.

Development checks: `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .`,
`uv run mypy`.

## Document pack

| # | Doc | For | What it holds |
|---|---|---|---|
| 00 | [Six-pager](docs/00-six-pager.md) | Management | Problem, options, proposal, plan, cost, risks, decisions needed, FAQ |
| 01 | [PRD](docs/01-prd.md) | Everyone | Scope, constraints, goals, use cases, verdict model, rules, functional and non-functional requirements |
| 02 | [Methodology](docs/02-methodology.md) | Builder | Every rule, algorithm, threshold and formula, with worked examples |
| 03 | [Architecture](docs/03-architecture.md) | Builder | Stack, package layout, interfaces, flows, budgets, config, testing |
| 04 | [Data sources](docs/04-data-sources.md) | Builder | Provider facts (checked Sept 2026) and the verification checklist |
| 05 | [Data model](docs/05-data-model.md) | Builder | SQLite schema as migrations, retention |
| 06 | [Acceptance tests](docs/06-acceptance-tests.md) | Builder, owner | AT-01 … AT-59: the definition of done |
| 07 | [Roadmap](docs/07-roadmap.md) | Owner, management | Phases P0–P11, releases, exit criteria, timeline |
| 08 | [Backlog](docs/08-backlog.md) | Builder | Ticket-sized work per phase |
| 09 | [Decisions](docs/09-decisions.md) | Owner | ADR log, seeded with the design's decisions |
| 10 | [Open questions](docs/10-open-questions.md) | Owner | What the owner needs to answer, with proposed answers |

## Claude Code kit

| Piece | What it does |
|---|---|
| [CLAUDE.md](CLAUDE.md) | Project memory loaded every session |
| [Playbook](docs/claude-code/playbook.md) | How the owner and Claude Code work: phase loop, practices, troubleshooting |
| [Phase prompts](docs/claude-code/phase-prompts.md) | Copy-paste opener per phase with its traps |
| `.claude/skills/` | `/start-phase`, `/next-ticket`, `/verify-source`, `/record-decision`, `/ask-owner`, `/phase-review` |
| `.claude/agents/` | `spec-reviewer`, `test-author`, `source-verifier` |
| `.claude/settings.json` | Pre-approved test/lint/git commands; `.env` reads blocked |

## Start building

1. Answer or accept Q-01 to Q-03 in [open questions](docs/10-open-questions.md).
2. Open Claude Code in this repo and run `/start-phase 0`.
3. Follow the [playbook](docs/claude-code/playbook.md).
