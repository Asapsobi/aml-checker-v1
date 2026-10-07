# amlcheck — Roadmap

> Twelve phases from an empty repo to v1.0, then four to v2.0 (D-070). Each phase is **one branch, one PR, one release**. Do them in
> order: a phase starts only when the previous one's exit criteria pass. Ticket-level work is in
> [08-backlog.md](08-backlog.md).

## Overview

| Phase | Goal | Release | Effort* | Usable result |
|---|---|---|---|---|
| **P0** Foundations & verification | Repo, tooling, CI, config, DB, verified data sources | — | 2–3 days | `amlcheck --version`, verification log |
| **P1** Chain data layer | Read USDT histories on both chains, cached and paced | v0.1.0 | 1 week | `amlcheck history <addr>` |
| **P2** Screening MVP | Sanctions + freezes + verdict + audit log | v0.2.0 | 1 week | **First real use**: `amlcheck check` |
| **P3** Exposure & behaviour | 1-hop exposure and behaviour rules, `labels.csv` | v0.3.0 | 1 week | Indirect risk caught |
| **P4** Intelligence store | Labels, entities, counterparty registry, look-alike guard | v0.4.0 | 1 week | The tool remembers |
| **P5** Profiler & classifier | Who an unknown address probably is | v0.5.0 | 1–1.5 weeks | `amlcheck classify` |
| **P6** Source-of-funds trace | 3-hop trace within budget | v0.6.0 | 2 weeks | `amlcheck trace` |
| **P7** Score & reports | 0–100 score, case report PDF | v0.7.0 | 1 week | One number + one file per counterparty |
| **P8** Operator UX | Web UI, batch, watchlist, audit export | v0.8.0 | 1–1.5 weeks | Daily-use comfort |
| **P9** Cases & decisions | Human decisions, feedback into labels | v0.9.0 | 1 week | Management-ready trail |
| **P10** Monitoring & API | Auto-screen senders to own wallets; local API | v0.10.0 | 1 week | Corridor integration |
| **P11** Calibration & 1.0 | Golden set, thresholds tuned, budgets measured | v1.0.0 | 1 week | Signed-off v1.0 |
| **P12** Risk policy v2 | MistTrack-style exposures, levels, decay; v2 contract | v2.0.0a1 | 1–1.5 weeks | Results read like a pro platform's |
| **P13** Two-way deep exposure | In + out, 5 hops best-first, all history | v2.0.0a2 | 1–1.5 weeks | Far and outgoing risk found |
| **P14** TRON intelligence | TRC20 first (D-083): UK/EU/NBCTF lists, freeze neighbours, Tronscan tags, deeper TRON coverage | v2.0.0b1 | 1.5 weeks | More risk named on TRON |
| **P15** Benchmark & 2.0 | Agreement with MistTrack measured on the owner's wallets; tuning | v2.0.0 | 1 week | Signed-off v2.0 |

\*Claude Code doing the work, owner reviewing each PR. A sizing guide, not a commitment.

```
Week:  1    2    3    4    5    6    7    8    9   10   11   12   13
P0     ██
P1       ████
P2           █████
P3                ████
P4                    █████
P5                         ██████
P6                               ██████████
P7                                         ████
P8                                             ██████
P9                                                   ████
P10                                                      ████
P11                                                          ████
       ▲ MVP check (wk 3)      ▲ classifier (wk 7)  ▲ trace (wk 9)        ▲ v1.0 (wk 13)
```

---

## Every phase ends with

- [ ] All tickets of the phase done (or moved to a question the owner agreed to)
- [ ] The phase's acceptance tests implemented and green
- [ ] `pytest`, `ruff check`, `ruff format --check`, `mypy` green; CI green on every job
- [ ] DB migrates from the previous release's schema; `audit verify` passes on an older DB
- [ ] `docs/verification-log.md`, `docs/acceptance-results.md`, [09-decisions.md](09-decisions.md),
      [10-open-questions.md](10-open-questions.md) updated
- [ ] README and `config.example.toml` cover every new command and key
- [ ] Version bumped, PR merged by the owner, tag, GitHub release

---

## P0 · Foundations & verification

| | |
|---|---|
| **Goal** | A clean, typed, tested skeleton and verified facts about every data source |
| **Deliverables** | `pyproject.toml` + `uv.lock`; package layout from [architecture §2](03-architecture.md); config loader with every section; migration runner; CLI stub; CI; `.env.example`; `config.example.toml`; `docs/verification-log.md` covering VS-01…VS-10 |
| **Exit criteria** | AT-01, AT-02; CI green on all jobs; verification log complete with fixtures saved; questions raised for anything that differs from [04-data-sources.md](04-data-sources.md) |

## P1 · Chain data layer

| | |
|---|---|
| **Goal** | Complete, cached, rate-safe USDT histories for any address on both chains |
| **Deliverables** | `chain/base.py`, `chain/tron.py`, `chain/bsc.py`, `chain/cache.py`, `net/limits.py`; migration 0001; `amlcheck history <addr> [--since] [--json]`; `cache stats/prune` |
| **Exit criteria** | AT-03…AT-06; live: a quiet and a busy address per chain, timings in `acceptance-results.md` |

## P2 · Screening MVP

| | |
|---|---|
| **Goal** | Answer "can I transact with this address?" with evidence and an audit trail |
| **Deliverables** | Address validation; OFAC sync + adapter; TRON freeze index + spot-check; BSC skipped adapter; rules + verdict; engine; audit log; `check`, `sync`, `status`, `audit list/verify`; migration 0002 |
| **Exit criteria** | AT-07…AT-21; live: an OFAC-listed address → BLOCK, a Tether-frozen TRON address → BLOCK, a never-used address → NO_HITS on each chain |

## P3 · Exposure & behaviour

| | |
|---|---|
| **Goal** | Catch indirect risk and suspicious behaviour |
| **Deliverables** | 1-hop walk; R-EXP-01/02; R-HEU-01…05; first-activity; `labels.csv` import; migration 0003 |
| **Exit criteria** | AT-22…AT-27; p95 < 60 s on a 1,000-transfer address |

## P4 · Intelligence store

| | |
|---|---|
| **Goal** | Remember counterparties and what is known about addresses |
| **Deliverables** | Categories; intel store; label packs; registry; look-alike guard (R-HEU-06); `cp`, `intel` commands; migration 0004 |
| **Exit criteria** | AT-28…AT-31 |

## P5 · Profiler & classifier

| | |
|---|---|
| **Goal** | Say who an unknown address probably is, with evidence |
| **Deliverables** | Contract lookup; `profile()`; `classify()`; entity linking; R-HEU-07; `classify`, `intel entity` commands; migration 0005 |
| **Exit criteria** | AT-32…AT-36; live: a known exchange hot wallet → HUB and one of its deposit addresses → DEPOSIT on each chain |

## P6 · Source-of-funds trace

| | |
|---|---|
| **Goal** | Answer "where did this money come from" within budget |
| **Deliverables** | Trace model, engine, peel annotation, R-TRC-01…05, adapter, job queue, layered SVG graph; `trace` and `investigate` commands; trace in checks ≥ threshold; migration 0006 |
| **Exit criteria** | AT-37…AT-41; live budget run (VS-07): 3 + 3 traces cold/warm within PRD G8 |

## P7 · Score & reports

| | |
|---|---|
| **Goal** | One number with a breakdown everywhere the verdict appears |
| **Deliverables** | `core/score.py`; score in record, CLI, JSON; optional R-SCR-01; case report PDF (`cp report`) |
| **Exit criteria** | AT-42…AT-44 |

## P8 · Operator UX

| | |
|---|---|
| **Goal** | Comfortable daily use |
| **Deliverables** | Web UI (check, history, detail, counterparties, trace with progress); `batch`; watchlist + scheduling docs; audit export CSV/JSON/PDF; migration 0007 |
| **Exit criteria** | AT-45…AT-48 |

## P9 · Cases & decisions

| | |
|---|---|
| **Goal** | Record the human decision and learn from it |
| **Deliverables** | Cases; decision hash chain; confirm/reject inferences; entity naming; decisions in exports and reports; `case export --jsonl`; web case page; migration 0008 |
| **Exit criteria** | AT-49…AT-51 |

## P10 · Monitoring & local API

| | |
|---|---|
| **Goal** | Counterparties found and screened without anyone typing; corridor integration |
| **Deliverables** | Own wallets; `monitor run` with lock and alerts; scheduling guides; local API (check, traces, counterparties, checks) with token, idempotency, problem+json; corridor mock script; server install guide; migration 0009 |
| **Exit criteria** | AT-52…AT-57 |

## P11 · Calibration & 1.0

| | |
|---|---|
| **Goal** | Prove the thresholds on real addresses and freeze the contract |
| **Deliverables** | Golden set (≥ 40 addresses per chain, recorded for offline replay); precision and band report; tuned defaults (formula changes bump versions); budget report; operator guide; JSON contract declared stable |
| **Exit criteria** | AT-58, AT-59; HUB/DEPOSIT precision ≥ 0.9, COLLECTOR ≥ 0.8; no clean golden address high/severe; owner sign-off |

---

## P12 · Risk policy v2

| | |
|---|---|
| **Goal** | Results that read like MistTrack's: levels, risk types, direct/indirect exposures with hops, volume and percent (D-070, D-071) |
| **Deliverables** | Exposure model; direct exposures both ways from the whole history read; indirect exposures from the trace with 40% hop decay; score v2 and levels; `INFO` severity and v2 verdict defaults (D-072); the checked address's own label; v2 JSON contract and `/v2` API (D-073); CLI, web, PDF and exports in v2; amounts shown to 2 decimals; counterparty table shows risk |
| **Exit criteria** | AT-60…AT-66; v1 records still render and verify |

## P13 · Two-way deep exposure

| | |
|---|---|
| **Goal** | Find risk that is far away or on the outgoing side (D-075, D-076) |
| **Deliverables** | Traced checks run both directions; best-first expansion to 5 hops with decay-aware pruning; all history up to a cap with the 180-day required window; budget re-measured |
| **Exit criteria** | AT-67, AT-68; live budget run within limits |

## P14 · Intelligence v2

| | |
|---|---|
| **Goal** | Name more of the risk entities MistTrack names, from sources we may use (D-077) |
| **Deliverables** | Each verified and licence-recorded first: UK and EU sanctions lists, Israel NBCTF seizure orders, official bridge/mixer/exchange addresses, explorer name tags if terms allow; derived "suspected malicious" addresses (inferred, never BLOCK) |
| **Exit criteria** | AT-69; every source in the verification log with its licence |

## P15 · Benchmark & 2.0

| | |
|---|---|
| **Goal** | Know how far results can be trusted next to MistTrack's |
| **Deliverables** | Benchmark set from the owner's wallets (the owner's MistTrack level per wallet, nothing else of theirs stored); level agreement and a reason for every mismatch; `k`, `decay` and weights tuned with decisions; operator guide v2 |
| **Exit criteria** | AT-70; owner sign-off |

---

## Dependencies

```
P0 ─► P1 ─► P2 ─► P3 ─► P4 ─► P5 ─► P6 ─► P7 ─► P8 ─► P9 ─► P10 ─► P11 ─► P12 ─► P13 ─► P14 ─► P15
                         └──────────────────────► P8 (web pages grow with each phase from P8 on)
```
P8 can start in parallel with P6/P7 if the owner wants the web UI earlier; it only needs P2–P4.
