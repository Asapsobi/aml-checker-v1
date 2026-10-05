# amlcheck

**Counterparty intelligence for USDT on TRON and BNB Smart Chain**: screening, source-of-funds tracing,
address classification, risk scoring and a recorded decision trail. Local-first, built on RPC
providers and indexers, no own nodes.

> **Status:** P11 (calibration & 1.0) ready for review, release **v1.0.0**. Released: v0.1.0 – v0.10.0. Calibration is partly measured; see [docs/calibration.md](docs/calibration.md). The product is built from these docs with Claude Code,
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

**End-to-end test (live).** `uv run python scripts/e2e.py` runs every feature through the real
commands on a fresh temporary data folder (sync, checks, investigate and trace, case report, cases,
batch, watchlist, monitor, exports with the hash chain re-verified, the API and the web UI) and prints
PASS / FAIL per step. It needs the keys and the network, and never touches `~/.amlcheck`. Don't run
other amlcheck commands at the same time: they share the TronGrid key's rate limit.

## Commands

| Command | What it does |
|---|---|
| `amlcheck check <addr> [--amount N] [--client NAME] [--note TEXT] [--trace/--no-trace] [--json]` | Screen an address: OFAC sanctions, Tether freezes on TRON (index + live `isBlackListed`), and its USDT history (the last 180 days required, older history as far as 20,000 transfers and 45 s allow): who it dealt with (sanctioned, frozen or labelled counterparties) and how it behaves (new, pass-through, fan-in, fan-out); and whether it only *looks like* a known counterparty or trusted address (address poisoning, R-HEU-06); and what kind of address it is (R-HEU-07 for a collector or distributor, low priority). More than 20,000 transfers in 180 days → INCOMPLETE. **Every check also traces where the money came from and where it went**, both at once, up to 5 hops, best first, within 3 minutes (methodology §12; `--no-trace` skips it). Running out of time leaves the rest `untraced:budget` and the check decidable; a provider error makes it INCOMPLETE. Every check says **who** the address is (its list entry, own wallet, entity or label, or the classifier's type marked inferred), lists its **exposures** (direct and indirect, received and sent, with hop, risk type, USDT and share) and has a **score** from 0 to 100 with a level `low` 0–30 / `moderate` 31–70 / `high` 71–90 / `severe` 91–100, as MistTrack reports them (methodology §11); BLOCK is 100, INCOMPLETE shows a lower bound (`≥ 34 · moderate+`). A score from 31 makes the check REVIEW (R-SCR-01, `[score] review_at`); behaviour and trace findings are `INFO`: they explain the score. Verdict `BLOCK` / `REVIEW` / `INCOMPLETE` / `NO_HITS`, recorded in the audit log before it is shown. Exit codes: 0 NO_HITS, 3 REVIEW, 4 INCOMPLETE, 5 BLOCK, 1 could not run |
| `amlcheck trace <addr> [--direction in\|out] [--svg FILE] [--json]` | Where an address's USDT came from (or went, with `out`), up to 3 hops: share per category (sanctioned, frozen, exchange, …) and the untraced share, coverage, top paths with their bottleneck amounts, and the budget used. Shares are estimates. No verdict and no audit record. Exit 0 complete, 4 partial, 1 could not start |
| `amlcheck investigate <addr> [--amount N] [--client NAME] [--svg FILE] [--json]` | A check with the trace always on, then the full trace breakdown and an optional graph (hop columns, category colours, edge width by amount). Same verdict and exit codes as `check` |
| `amlcheck batch <file.csv> [--out results.csv]` | Screen a CSV (`address,chain,amount,client,note`, only `address` required) one row at a time. Every row is validated first: one bad row and nothing is screened. Results stream out as CSV (`row,address,chain,verdict,score,level,rules,check_id,record_hash`); exit code = the most serious verdict |
| `amlcheck watch add <addr> [--client] [--note]` / `watch remove` / `watch list` / `watch run` | A watchlist: `watch run` re-screens every watched address (no trace); a changed verdict is listed, shown as a macOS notification (and POSTed to `[monitor] webhook_url` if set) and the run exits **6**. See [scheduling](docs/scheduling.md) to run it daily |
| `amlcheck case open <addr>` / `case decide <id> approved\|rejected\|escalated --note …` / `case list` / `case show <id>` | Cases on REVIEW, BLOCK or INCOMPLETE checks; decisions with a note and your name (`[operator] name` or `--by`) go into their own hash chain, tied to the check record. `approved`/`rejected` close the case, `escalated` keeps it open. `audit verify` checks both chains |
| `amlcheck case confirm <id> TYPE [--category C] [--name N --kind K]` / `case reject <id> TYPE` / `case export --jsonl` | Confirm an inferred type (DEPOSIT joins its hub's entity, HUB names it, COLLECTOR/DISTRIBUTOR/PASS_THROUGH become a label in a category you pick) or reject it (not assigned again until the classifier changes); export every decision with its evidence as JSON lines |
| `amlcheck wallets add <addr> --name N` / `wallets remove` / `wallets list` | Your own wallets: labelled trusted, never screened as senders, and protected by the look-alike guard |
| `amlcheck monitor run` | Screens new senders to your own wallets (since the last run; 24 h on the first): senders screened in the last 7 days are skipped, every sender is traced like a check, at most 10 per run (the next run continues). Exit **6** when a sender is REVIEW, BLOCK or INCOMPLETE. Run it every 10 minutes ([scheduling](docs/scheduling.md)) |
| `amlcheck api [--port 8766]` | The local API for the corridor ([docs/api.md](docs/api.md)): `POST /v2/check`, `GET /v2/checks/{id}`, `POST /v2/traces`, `GET /v2/traces/{id}`, `GET /v2/counterparties/{chain}/{address}`; Bearer token `AMLCHECK_API_TOKEN` (≥ 32 characters, in `.env`), `Idempotency-Key`, problem+json errors. Server install: [docs/server.md](docs/server.md) |
| `amlcheck web [--port 8765]` | Local web UI on 127.0.0.1: check form, history, check detail with explorer links and the case report, counterparties, traces with progress and graph. Open the sign-in address it prints; no scripts, no CDN |
| `amlcheck labels import labels.csv` / `labels list` | Your own address tags (`address,chain,tag,note,source`): `mixer`, `bridge`, `high_risk` raise R-HEU-05 and count as flagged; `allowlist` leaves a counterparty out of the behaviour rules. One bad row and nothing is imported |
| `amlcheck cp list [--chain] [--verdict] [--client]` / `cp show <addr>` / `cp rebuild` | The counterparty registry: every address checked, with its last verdict, score and clients; `rebuild` recreates it from the audit log |
| `amlcheck cp report <addr> [--check ID] [--out FILE]` | Case report PDF of the latest (or chosen) check: verdict, score, findings, sources, USDT history, classification, decision, and the source of funds with its graph. Built from stored records only; marked internal use only |
| `amlcheck intel label <addr> <category>` / `intel retract <id> --reason` / `intel show <addr> [--all]` / `intel stats` | Operator labels (methodology §8 categories), retracted but never deleted |
| `amlcheck intel import-pack <file> --name N --licence TEXT` | A licensed third-party label pack (`address,chain,category,note`); refused without a licence |
| `amlcheck classify <addr> [--json]` | Who an address probably is (HUB, DEPOSIT, COLLECTOR, DISTRIBUTOR, PASS_THROUGH, PERSONAL, CONTRACT, FRESH), with the conditions that held. Deposits are linked to their hub's entity. Inferences, never a verdict |
| `amlcheck intel entity list` / `entity show <id>` / `entity name <id> <name> --kind K` | Groups of addresses with one owner; naming a hub (e.g. an exchange) carries its kind to its deposits |
| `amlcheck sync` | Download the sanctions lists, OFAC (about 3 MB zipped), the UK Sanctions List and the EU list (about 22 and 26 MB), and refresh the Tether TRON freeze index. **Run at least daily**: a list older than 48 h makes checks INCOMPLETE. A listing on any of them BLOCKs (methodology §13.1) |
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
| — | [Calibration](docs/calibration.md) | Owner | What the golden set measured, what it didn't yet, and the calibration notes |
| — | [Operator guide](docs/operator-guide.md) | Operator | The daily rhythm, reading a result, findings, cases, intelligence, troubleshooting |
| — | [Scheduling](docs/scheduling.md) | Operator | Daily `sync` and `watch run`, `monitor run` every 10 minutes, with launchd, cron or a systemd timer |
| — | [API](docs/api.md) | Integrator | The local API: auth, idempotency, endpoints, errors, the corridor mock |
| — | [Server](docs/server.md) | Operator | Running the API and the timers under systemd on the corridor server |

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
