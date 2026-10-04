# amlcheck — Backlog

> Ticket-sized work for Claude Code. One ticket ≈ one focused session and one commit (or a few).
> Take them in order within a phase; **Needs** lists hard dependencies. Use `/next-ticket` to pick the
> next one and `/start-phase` to begin a phase ([claude-code/playbook.md](claude-code/playbook.md)).
>
> **Size:** S < 2 h · M ≈ half a day · L ≈ a day or more (split it if it grows).
> **Status** is tracked in the PR description, not here: keep this file the plan.

---

## P0 · Foundations & verification

| ID | Ticket | Size | Needs | Done when |
|---|---|---|---|---|
| T-0.01 | Project setup: `pyproject.toml`, `uv.lock`, `.python-version`, `.gitignore`, ruff/mypy/pytest config | S | — | `uv sync` works; empty test run green |
| T-0.02 | Package skeleton per architecture §2 with empty modules and docstrings naming their phase | S | T-0.01 | `mypy` and `ruff` green on the skeleton |
| T-0.03 | `core/clock.py`, `core/models.py` (enums + dataclasses only) | S | T-0.02 | Unit tests for ISO round-trips |
| T-0.04 | `config.py`: every section in architecture §6 with defaults from the methodology; `AMLCHECK_HOME`/`AMLCHECK_CONFIG`; `.env` order; config hash | M | T-0.02 | AT-02 |
| T-0.05 | `storage/db.py`: connect (WAL), `user_version` migration runner, empty `0001` placeholder | S | T-0.02 | Migrates an empty DB; re-run is a no-op |
| T-0.06 | CLI stub: `--version`, `status` (home, DB version, config path) | S | T-0.04, T-0.05 | AT-01 |
| T-0.07 | CI workflow: Ubuntu + macOS × 3.12/latest, Windows 3.12; actions pinned to SHAs | S | T-0.01 | CI green |
| T-0.08 | `.env.example`, `config.example.toml`, README "Setup" | S | T-0.04 | Files match config fields |
| T-0.09 | Verification VS-01…VS-06, VS-08…VS-10: run live, save fixtures, write `docs/verification-log.md` | L | — | Every item: date, URL, finding, what it changes |
| T-0.10 | Raise questions for every difference from `04-data-sources.md` | S | T-0.09 | Listed in `10-open-questions.md` |

## P1 · Chain data layer

| ID | Ticket | Size | Needs | Done when |
|---|---|---|---|---|
| T-1.00 | `core/address.py` pulled forward from T-2.01 (D-038): detect, base58check, EIP-55, normalise, TRON hex↔base58 | M | P0 | Unit tests; AT-07/AT-08 address parts |
| T-1.01 | `net/http.py`: shared client, timeouts, Retry-After policy (≤ 10 s wait, else error), 403≙429 for TronGrid | M | P0 | Unit tests with respx |
| T-1.02 | `net/limits.py`: token bucket + header-driven budget pacer (≤ 65 s wait) | M | T-1.01 | AT-06 |
| T-1.03 | `chain/base.py`: `Transfer`, `History`, `HistorySource`, `ContractLookup` protocols | S | P0 | Typed, documented |
| T-1.04 | `chain/tron.py`: TRC20 transfers (paged, newest first, limit, window), 6-decimal amounts, hex→base58 helper | L | T-1.01, T-1.03 | Fixture tests incl. empty answer |
| T-1.05 | `chain/bsc.py`: HyperSync Transfer logs by address (both directions), oldest-first paging, newest-first windows from head, hex timestamps, 18 decimals, DNS fallback URL | L | T-1.02, T-1.03 | Fixture tests incl. no-progress error |
| T-1.06 | Migration `0001_cache.sql` | S | T-0.05 | Schema matches data model |
| T-1.07 | `chain/cache.py`: covered windows, gap computation, incremental fetch, zero-value handling, incomplete windows | L | T-1.04, T-1.05, T-1.06 | AT-03, AT-04, AT-05 |
| T-1.08 | `amlcheck history`, `cache stats`, `cache prune` | S | T-1.07 | CLI tests |
| T-1.09 | Live run: quiet + busy address per chain; record timings | S | T-1.08 | `acceptance-results.md` |

## P2 · Screening MVP

| ID | Ticket | Size | Needs | Done when |
|---|---|---|---|---|
| T-2.01 | ~~`core/address.py`~~ done in P1 (T-1.00, D-038); P2 wires it into `check` | S | P1 | AT-07, AT-08 |
| T-2.02 | Migration `0002_screening.sql` | S | P1 | Schema matches |
| T-2.03 | `core/audit.py`: canonical JSON, append, verify, "hash only when set" | M | T-2.02 | AT-20 |
| T-2.04 | `core/rules.py` + `core/verdict.py`: IDs, defaults, overrides, fixed rules, R-SYS-01 | M | T-0.03 | Verdict precedence tests; AT-16 |
| T-2.05 | `screening/sanctions.py`: download, parse every digital-currency id, snapshot, sanity check, adapter, staleness | L | T-2.02 | AT-10, AT-15, AT-21 |
| T-2.06 | `screening/tron_freeze.py`: event indexer (incremental, confirmed only, `deprecated()` check, lag → stale), `isBlackListed`, adapter | L | T-2.02, T-1.04 | AT-13, AT-17, AT-18 |
| T-2.07 | ~~`screening/evm_freeze.py`~~ Dropped (D-039): v1 covers TRC20 and BEP20 only | — | — | — |
| T-2.08 | `screening/bsc_freeze.py`: always skipped with reason (BEP20 USDT can't freeze; other chains not checked in v1) | S | T-0.03 | AT-19 |
| T-2.09 | `core/engine.py`: concurrent sources, timeouts → `failed()`, rules, verdict, audit append before output | M | T-2.03…T-2.08 | AT-09, AT-16 |
| T-2.10 | CLI `check` (human + `--json`, exit codes, disclaimer), `sync`, `status`, `audit list/verify` | M | T-2.09 | CLI tests |
| T-2.11 | Live: OFAC-listed, Tether-frozen, never-used addresses on both chains | S | T-2.10 | `acceptance-results.md` |

## P3 · Exposure & behaviour

| ID | Ticket | Size | Needs | Done when |
|---|---|---|---|---|
| T-3.01 | VS-10 first-activity definitions confirmed live | S | P2 | Logged |
| T-3.02 | `screening/heuristics.py`: FIFO pass-through, busiest window (pure) | M | P2 | Unit tests incl. edge cases |
| T-3.03 | Migration `0003_labels.sql`; `intel/labels_csv.py` import (all-or-nothing) | M | P2 | AT-27 |
| T-3.04 | `screening/exposure.py`: history read via cache, counterparties, local flags, R-EXP-01/02, finding caps | L | T-3.03 | AT-22, AT-23, AT-26 |
| T-3.05 | R-HEU-01…05 in the exposure source, allowlist exclusion | M | T-3.02, T-3.04 | AT-24, AT-25 |
| T-3.06 | Human output: counterparties table, behaviour findings | S | T-3.05 | Snapshot tests |
| T-3.07 | Performance check: 1,000-transfer fixture < 60 s p95 | S | T-3.05 | Recorded |

## P4 · Intelligence store

| ID | Ticket | Size | Needs | Done when |
|---|---|---|---|---|
| T-4.01 | Migration `0004_intel.sql` | S | P3 | Schema matches |
| T-4.02 | `intel/categories.py` (methodology §8, versioned) | S | P3 | Unknown category refused |
| T-4.03 | `intel/store.py`: labels add/retract/list, `labels.csv` mapping, entities, membership, `best_terminal()` | L | T-4.01, T-4.02 | AT-31; ordering tests for best_terminal |
| T-4.04 | `intel/packs.py` with mandatory licence; VS-14 template | S | T-4.03 | AT-30 |
| T-4.05 | `intel/registry.py`: upsert after audit append, query, rebuild | M | T-4.01 | AT-28 |
| T-4.06 | `intel/lookalike.py` + R-HEU-06 source | M | T-4.05 | AT-29 |
| T-4.07 | CLI `cp list/show/rebuild`, `intel label/retract/show/stats/import-pack` | M | T-4.03…T-4.06 | CLI tests |

## P5 · Profiler & classifier

| ID | Ticket | Size | Needs | Done when |
|---|---|---|---|---|
| T-5.01 | VS-12, VS-13 contract detection verified | S | P4 | Logged with fixtures |
| T-5.02 | `ContractLookup` for TRON and BSC, cached in `contracts` | M | T-5.01 | Fixture tests |
| T-5.03 | `profile/features.py`: every feature, pure | M | P4 | Unit tests per feature |
| T-5.04 | Migration `0005_classifier.sql` | S | P4 | Schema matches |
| T-5.05 | `profile/classifier.py`: types, bonuses, primary, FRESH tag, version, expiry | L | T-5.03 | AT-32, AT-34, AT-36 |
| T-5.06 | Entity linking for DEPOSIT, auto-named entities | M | T-5.05, T-4.03 | AT-33 |
| T-5.07 | `profile/adapter.py` + R-HEU-07; config refuses BLOCK | S | T-5.05 | AT-34, AT-35 |
| T-5.08 | CLI `classify`, `intel entity list/show/name` | S | T-5.06 | CLI tests |
| T-5.09 | Live: exchange hot wallet + deposit per chain | S | T-5.08 | `acceptance-results.md` |

## P6 · Source-of-funds trace

| ID | Ticket | Size | Needs | Done when |
|---|---|---|---|---|
| T-6.01 | Migration `0006_trace.sql`; `trace/model.py` with JSON round trip (Decimals as strings) | M | P5 | Round-trip tests |
| T-6.02 | `trace/engine.py` part 1: windows, weights, pruning, local terminal tests (1–7) | L | T-6.01 | Unit tests per rule |
| T-6.03 | `trace/engine.py` part 2: reads, read terminals (8–10), expansion, cycles, revisits, budgets, failures | L | T-6.02 | AT-37, AT-39, AT-40, AT-41 |
| T-6.04 | Property tests with hypothesis | M | T-6.03 | AT-38 |
| T-6.05 | `trace/annotate.py` peel chains | M | T-6.03 | Unit tests |
| T-6.06 | `trace/rules.py` R-TRC-01…05 with path evidence | M | T-6.03 | AT-37 findings |
| T-6.07 | `trace/adapter.py`; trace in checks ≥ threshold or on request | S | T-6.06 | Engine test |
| T-6.08 | `trace/jobs.py`: queue, one worker, progress, resume | M | T-6.07 | Restart test |
| T-6.09 | `trace/graph.py` layered SVG with legend, 8+6 labels | M | T-6.03 | Snapshot test |
| T-6.10 | CLI `trace`, `investigate`; human output: exposure table, coverage, top paths | M | T-6.08, T-6.09 | CLI tests |
| T-6.11 | VS-07 live budget run (3 + 3, cold/warm) | S | T-6.10 | Within G8; recorded |

## P7 · Score & reports

| ID | Ticket | Size | Needs | Done when |
|---|---|---|---|---|
| T-7.01 | `core/score.py` (half-up rounding, lower bound, components) | M | P6 | AT-42, AT-43 |
| T-7.02 | Score in engine, audit (`score_json` hashed when set), registry | S | T-7.01 | AT-44 |
| T-7.03 | Optional R-SCR-01 | S | T-7.02 | Off by default; test when on |
| T-7.04 | Score + breakdown in human and JSON output | S | T-7.02 | Snapshot tests |
| T-7.05 | `report/case_report.py` + `cp report` | M | T-7.04 | PDF opens; contents test |

## P8 · Operator UX

| ID | Ticket | Size | Needs | Done when |
|---|---|---|---|---|
| T-8.01 | Web app shell: host allow-list, start-up token, CSP, vendored HTMX | M | P7 | AT-48 |
| T-8.02 | Pages: check form, result, history, detail (explorer links) | M | T-8.01 | Web tests |
| T-8.03 | Pages: counterparties, counterparty detail (profile, labels, entity), trace view with progress | L | T-8.02 | Web tests |
| T-8.04 | `batch` (validate all, screen one at a time, stream CSV) | M | P7 | AT-45 |
| T-8.05 | Migration `0007_ops.sql`; watchlist commands; `watch run` exit 6; macOS notification | M | P7 | AT-46 |
| T-8.06 | `report/export.py` CSV/JSON/PDF with CSV-injection guard | M | P7 | AT-47 |
| T-8.07 | `docs/scheduling.md` (launchd, cron, systemd timer) | S | T-8.05 | Reviewed |

## P9 · Cases & decisions

| ID | Ticket | Size | Needs | Done when |
|---|---|---|---|---|
| T-9.01 | Migration `0008_cases.sql`; `[operator] name` | S | P8 | Schema matches |
| T-9.02 | `cases/decisions.py` chain + verify; `audit verify` covers both | M | T-9.01 | AT-49 |
| T-9.03 | `cases/cases.py`: open, list, show, close | M | T-9.02 | CLI tests |
| T-9.04 | Confirm/reject inferences; suppression in classifier; entity naming | M | T-9.03 | AT-50, AT-51 |
| T-9.05 | Decisions in exports and case report; `case export --jsonl` | S | T-9.04 | Tests |
| T-9.06 | Web case page with decision form | M | T-9.04 | Web tests |

## P10 · Monitoring & local API

| ID | Ticket | Size | Needs | Done when |
|---|---|---|---|---|
| T-10.01 | Migration `0009_monitor_api.sql`; `wallets add/remove/list` | S | P9 | Schema matches |
| T-10.02 | `monitor/inbound.py`: lock, new senders, rescreen policy, trace threshold, alerts, optional local webhook | L | T-10.01 | AT-52, AT-53, AT-54 |
| T-10.03 | API shell: token (≥ 32 chars), host allow-list, problem+json | M | P9 | AT-57 |
| T-10.04 | `POST /v1/check`, `GET /v1/checks/{id}` with idempotency | M | T-10.03 | AT-55 |
| T-10.05 | `POST /v1/traces`, `GET /v1/traces/{id}`, `GET /v1/counterparties/{chain}/{address}` | M | T-10.04 | AT-56 |
| T-10.06 | `docs/api.md`, `docs/server.md` (systemd), `scripts/corridor_mock.py` | S | T-10.05 | Reviewed |

## P11 · Calibration & 1.0

| ID | Ticket | Size | Needs | Done when |
|---|---|---|---|---|
| T-11.01 | Golden set: choose ≥ 40 addresses per chain with the owner; record histories | L | P10 | `tests/golden/` |
| T-11.02 | Offline replay harness + precision/band report | M | T-11.01 | Report generated |
| T-11.03 | Live golden run; tune config defaults; decisions recorded | M | T-11.02 | AT-58 |
| T-11.04 | Budget report (checks, traces, cache hit rate) | S | T-11.03 | AT-59 |
| T-11.05 | Operator guide; JSON contract declared stable; README complete | M | T-11.04 | Owner sign-off |

## P12 · Risk policy v2

| ID | Ticket | Size | Needs | Done when |
|---|---|---|---|---|
| T-12.01 | Methodology §11, decisions D-070…D-077, roadmap, AT-60…AT-70, Q-37 | S | P11 | Reviewed |
| T-12.02 | `core/risk.py`: exposure model, risk types and weights; direct exposures both ways in the exposure source's evidence | M | T-12.01 | AT-60 |
| T-12.03 | Indirect exposures from the trace, with hop decay | M | T-12.02 | AT-61 |
| T-12.04 | Score v2 and levels; `INFO` severity; v2 verdict defaults (R-SCR-01 at 31) | L | T-12.03 | AT-62, AT-63 |
| T-12.05 | The checked address's own label | S | T-12.02 | AT-65 |
| T-12.06 | v2 JSON contract and `/v2` API (`/v1` → 410); CLI, web, PDF and exports | L | T-12.04 | AT-64 |
| T-12.07 | Amounts shown to 2 decimals; the counterparty table shows risk types | S | T-12.06 | AT-66 |
| T-12.08 | Golden set and e2e on v2; live runs; `2.0.0a1` | M | T-12.07 | Acceptance results |

## P13 · Two-way deep exposure

| ID | Ticket | Size | Needs | Done when |
|---|---|---|---|---|
| T-13.01 | Best-first trace to 5 hops with decay-aware pruning | L | P12 | AT-67 |
| T-13.02 | Traced checks run both directions | M | T-13.01 | AT-67 |
| T-13.03 | All history up to a cap; the 180-day required window | M | P12 | AT-68 |
| T-13.04 | Live budget run; methodology and docs | S | T-13.03 | Budget report |

## P14 · Intelligence v2

| ID | Ticket | Size | Needs | Done when |
|---|---|---|---|---|
| T-14.01 | Verify UK, EU and NBCTF lists (format, licence); add as list sources | L | P13 | AT-69 |
| T-14.02 | Official bridge, mixer and exchange addresses, each with its source | M | P13 | Verification log |
| T-14.03 | Explorer name tags, only if the terms allow (else recorded and dropped) | M | P13 | Decision recorded |
| T-14.04 | Derived "suspected malicious" addresses from our traces | M | P13 | AT-69 |

## P15 · Benchmark & 2.0

| ID | Ticket | Size | Needs | Done when |
|---|---|---|---|---|
| T-15.01 | Benchmark set from the owner's wallets (Q-37); agreement report | M | P14 | AT-70 |
| T-15.02 | Tune `k`, `decay` and weights; decisions recorded | M | T-15.01 | AT-70 |
| T-15.03 | Operator guide v2; README; release `2.0.0` | S | T-15.02 | Owner sign-off |

