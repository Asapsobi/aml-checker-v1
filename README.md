# amlcheck

**Counterparty intelligence for USDT on TRON and BNB Smart Chain**: screening, source-of-funds tracing,
address classification, risk scoring and a recorded decision trail. Local-first, built on RPC
providers and indexers, no own nodes.

> **Status:** P8 (operator UX) ready for review, release v0.8.0. Released: v0.1.0 – v0.7.0. The product is built from these docs with Claude Code,
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

## Commands

| Command | What it does |
|---|---|
| `amlcheck check <addr> [--amount N] [--client NAME] [--note TEXT] [--trace/--no-trace] [--json]` | Screen an address: OFAC sanctions, Tether freezes on TRON (index + live `isBlackListed`), and its 180-day USDT history: who it dealt with (sanctioned, frozen or labelled counterparties) and how it behaves (new, pass-through, fan-in, fan-out); and whether it only *looks like* a known counterparty or trusted address (address poisoning, R-HEU-06); and what kind of address it is (R-HEU-07 for a collector or distributor, low priority). More than 5,000 transfers in 180 days → INCOMPLETE. With `--trace`, or automatically for `--amount` of 10,000 USDT or more, the source-of-funds trace runs too (R-TRC-01…05); a trace that could not finish makes the check INCOMPLETE. Every check has a **score** from 0 to 100 with its breakdown (exposure E, direct D, behaviour B, uncertainty U) and a band `low` / `medium` / `high` / `severe`; BLOCK is 100, INCOMPLETE shows a lower bound (`≥ 34 · medium+`). The score never changes the verdict unless you set `[score] review_at` (R-SCR-01). Verdict `BLOCK` / `REVIEW` / `INCOMPLETE` / `NO_HITS`, recorded in the audit log before it is shown. Exit codes: 0 NO_HITS, 3 REVIEW, 4 INCOMPLETE, 5 BLOCK, 1 could not run |
| `amlcheck trace <addr> [--direction in\|out] [--svg FILE] [--json]` | Where an address's USDT came from (or went, with `out`), up to 3 hops: share per category (sanctioned, frozen, exchange, …) and the untraced share, coverage, top paths with their bottleneck amounts, and the budget used. Shares are estimates. No verdict and no audit record. Exit 0 complete, 4 partial, 1 could not start |
| `amlcheck investigate <addr> [--amount N] [--client NAME] [--svg FILE] [--json]` | A check with the trace always on, then the full trace breakdown and an optional graph (hop columns, category colours, edge width by amount). Same verdict and exit codes as `check` |
| `amlcheck batch <file.csv> [--out results.csv]` | Screen a CSV (`address,chain,amount,client,note`, only `address` required) one row at a time. Every row is validated first: one bad row and nothing is screened. Results stream out as CSV (`row,address,chain,verdict,score,band,rules,check_id,record_hash`); exit code = the most serious verdict |
| `amlcheck watch add <addr> [--client] [--note]` / `watch remove` / `watch list` / `watch run` | A watchlist: `watch run` re-screens every watched address (no trace); a changed verdict is listed, shown as a macOS notification (and POSTed to `[monitor] webhook_url` if set) and the run exits **6**. See [scheduling](docs/scheduling.md) to run it daily |
| `amlcheck web [--port 8765]` | Local web UI on 127.0.0.1: check form, history, check detail with explorer links and the case report, counterparties, traces with progress and graph. Open the sign-in address it prints; no scripts, no CDN |
| `amlcheck labels import labels.csv` / `labels list` | Your own address tags (`address,chain,tag,note,source`): `mixer`, `bridge`, `high_risk` raise R-HEU-05 and count as flagged; `allowlist` leaves a counterparty out of the behaviour rules. One bad row and nothing is imported |
| `amlcheck cp list [--chain] [--verdict] [--client]` / `cp show <addr>` / `cp rebuild` | The counterparty registry: every address checked, with its last verdict, score and clients; `rebuild` recreates it from the audit log |
| `amlcheck cp report <addr> [--check ID] [--out FILE]` | Case report PDF of the latest (or chosen) check: verdict, score, findings, sources, USDT history, classification, decision, and the source of funds with its graph. Built from stored records only; marked internal use only |
| `amlcheck intel label <addr> <category>` / `intel retract <id> --reason` / `intel show <addr> [--all]` / `intel stats` | Operator labels (methodology §8 categories), retracted but never deleted |
| `amlcheck intel import-pack <file> --name N --licence TEXT` | A licensed third-party label pack (`address,chain,category,note`); refused without a licence |
| `amlcheck classify <addr> [--json]` | Who an address probably is (HUB, DEPOSIT, COLLECTOR, DISTRIBUTOR, PASS_THROUGH, PERSONAL, CONTRACT, FRESH), with the conditions that held. Deposits are linked to their hub's entity. Inferences, never a verdict |
| `amlcheck intel entity list` / `entity show <id>` / `entity name <id> <name> --kind K` | Groups of addresses with one owner; naming a hub (e.g. an exchange) carries its kind to its deposits |
| `amlcheck sync` | Download the OFAC list (about 29 MB) and refresh the Tether TRON freeze index. **Run at least daily**: a list older than 48 h makes checks INCOMPLETE |
| `amlcheck status [--json]` | Data folder, config, database, source freshness and the audit log head |
| `amlcheck audit list [--address] [--verdict] [--client] [--since] [--json]` | Recorded checks with their score, newest first |
| `amlcheck audit export --format csv\|json\|pdf [--address] [--verdict] [--client] [--since] [--until] [--out FILE]` | Export the audit log: CSV safe to open in a spreadsheet (formula-like cells prefixed with `'`), JSON with every hash so the chain re-verifies from the file, or a PDF table |
| `amlcheck audit verify` | Recompute the audit hash chain; prints the head hash to keep elsewhere, or the first broken record (exit 1) |
| `amlcheck history <addr> [--since 30\|2026-09-01] [--until …] [--limit N] [--first-activity] [--json]` | An address's USDT transfers, newest first, from the cache and the providers. 0-value spam is dropped and counted; a window with more than `--limit` transfers is marked incomplete |
| `amlcheck cache stats [--json]` | Addresses, windows and transfers cached per chain |
| `amlcheck cache prune [--days N]` | Forget histories of addresses not used for N days (default `[cache] history_keep_days`) |

Errors exit with code 1 and a one-line `error:` message. TRON works without a key at 1 request/s;
BSC history needs `AMLCHECK_HYPERSYNC_TOKEN`. v1 covers USDT on TRC20 and BEP20 only: BEP20 USDT
can't be frozen, and BSC results say that freezes on other chains are not checked (D-039).
`NO_HITS` means nothing was found in the sources checked; it is not a clearance.

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
| — | [Scheduling](docs/scheduling.md) | Operator | Daily `sync` and `watch run` with launchd, cron or a systemd timer |

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
