# amlcheck — Acceptance tests

> The tests that define "done". Each is automated against recorded fixtures (never the network) unless
> marked **live**. When a phase is built, Claude Code maps each ID to its test names and records live
> results in `docs/acceptance-results.md`.

| ID | Phase | Input | Expected |
|---|---|---|---|
| AT-01 | P0 | Empty `AMLCHECK_HOME` | `amlcheck --version` and `amlcheck status` run; DB created at the latest schema |
| AT-02 | P0 | Config with an unknown key or invalid value | Refused at load with a clear message, exit 1 |
| AT-03 | P1 | Read the same address twice within the window; fake source records windows asked | Second read asks only for the gap after the first read |
| AT-04 | P1 | Window holds more than the limit | Newest `limit` transfers returned, window marked incomplete |
| AT-05 | P1 | 0-USDT transfers in the answer | Dropped and counted in `zero_value` |
| AT-06 | P1 | Indexer answers 429 with budget headers | Pacer waits for the window and succeeds; wait > 65 s → error |
| AT-07 | P2 | Invalid address string | Error, no audit record, exit 1 |
| AT-08 | P2 | Mixed-case EVM address with a bad EIP-55 checksum | Error |
| AT-09 | P2 | Valid TRON address, all sources clean | `NO_HITS`, disclaimer printed, audit record written, exit 0 |
| AT-10 | P2 | Address in the OFAC fixture (listed under another currency label) | `BLOCK`, R-SAN-01 with entry ID and entity name, exit 5 |
| AT-11 | P2 | ~~BSC target frozen on another EVM chain~~ | Dropped with the EVM index (D-039) |
| AT-12 | P2 | ~~EVM index: add then removal~~ | Dropped (D-039); R-FRZ-02 is covered on TRON by AT-17 |
| AT-13 | P2 | TRON freeze index can't be refreshed and lags the chain > 60 min | `INCOMPLETE`, R-SYS-01 naming the source |
| AT-14 | P2 | 429 with `Retry-After: 3`, then OK | Waits, succeeds. With `Retry-After: 30` → `INCOMPLETE` |
| AT-15 | P2 | Sanctions snapshot downloaded 49 h ago | `INCOMPLETE` unless a BLOCK finding exists |
| AT-16 | P2 | OFAC hit **and** TRON freeze index down | `BLOCK` (precedence) |
| AT-17 | P2 | TRON index: latest event `AddedBlackList` | `BLOCK`; `RemovedBlackList` after it → `REVIEW` R-FRZ-02 |
| AT-18 | P2 | TRON contract `deprecated()` returns true | Freeze index source `error` → `INCOMPLETE` |
| AT-19 | P2 | Any BSC check | Token freeze source `skipped` with reason, incl. that other chains are not checked in v1; can end `NO_HITS` |
| AT-20 | P2 | Tamper one `checks` row | `audit verify` reports the break at that record |
| AT-21 | P2 | New snapshot with 30% fewer addresses | Rejected, previous snapshot kept, warning logged |
| AT-22 | P3 | Fixture: target received 1,200 USDT from a frozen address | `REVIEW`, R-EXP-01 with tx evidence |
| AT-23 | P3 | 6% of inflow from flagged counterparties | R-EXP-02 |
| AT-24 | P3 | Pass-through fixture (95% out within 2 h) | R-HEU-02; same counterparty `allowlist`-labelled → not raised |
| AT-25 | P3 | 60 senders of 20 USDT within 3 h | R-HEU-03 |
| AT-26 | P3 | History of 5,001 transfers in the window | Exposure `stale` → `INCOMPLETE` |
| AT-27 | P3 | `labels import` with one bad row | Nothing imported, exit 1, previous labels intact |
| AT-28 | P4 | Registry after 10 checks over 4 addresses, then `cp rebuild` | Identical rows |
| AT-29 | P4 | Target shares first 4 and last 4 body characters with a registry address | `REVIEW`, R-HEU-06 naming both full addresses |
| AT-30 | P4 | `intel import-pack` without `--licence` | Refused, nothing imported |
| AT-31 | P4 | Add then retract an operator label | Active then retracted; still listed with `--all` |
| AT-32 | P5 | 600 distinct counterparties | `HUB` 0.7; capped read → 0.95 |
| AT-33 | P5 | Deposit sweeping 99.5% to a hub within 6 h from 8 senders; then name the hub's entity "Binance" `exchange_regulated` | `DEPOSIT` linked to the hub's entity; both resolve to `exchange_regulated` |
| AT-34 | P5 | 120 senders, 92% of inbound < 100 USDT, 85% forwarded to one address | `COLLECTOR` ≥ 0.7 → R-HEU-07 (low) |
| AT-35 | P5 | Config raises R-HEU-07 to BLOCK | Refused at load |
| AT-36 | P5 | Each classifier condition at and just past its threshold | Fires / does not fire exactly at the boundary |
| AT-37 | P6 | Methodology §7.11 fixture | Partition `{exchange_regulated 0.60, sanctioned 0.20, suspicious_collector 0.10, untraced:pruned 0.10}`; coverage 0.90; R-TRC-01 (bottleneck 4,000), R-TRC-03, R-TRC-05; 3 addresses read (T, B, E; A resolves from its named entity, D-047) |
| AT-38 | P6 | 500 random synthetic graphs (property test) | Partition = 1 ± 0.001; reads ≤ `max_nodes`; identical JSON on re-run |
| AT-39 | P6 | A node's history read fails | `INCOMPLETE`, R-SYS-01 naming the node; partial trace saved |
| AT-40 | P6 | `max_nodes = 2` on the §7.11 fixture (3 reads there, D-047) | Remaining weight in `untraced:budget`; not INCOMPLETE |
| AT-41 | P6 | Cycle A → B → A | Weight to `untraced:cycle`; terminates |
| AT-42 | P7 | §7.11 fixture with R-HEU-01 | Score 66, band `high`, components E 59.5, D 0, B 5, U 1 |
| AT-43 | P7 | A BLOCK check; an INCOMPLETE check with E = 34 | 100; `≥ 34` band `medium+` |
| AT-44 | P7 | Records written before and after `score_json` existed | All verify |
| AT-45 | P8 | Batch of 100 rows with one invalid row | Nothing screened, exit 1. Fixed file: all screened within rate limits, results streamed |
| AT-46 | P8 | `watch run` where one verdict changed | Change reported, recorded, exit 6 |
| AT-47 | P8 | Audit export CSV with a cell starting `=` | Cell prefixed with `'` (CSV injection) |
| AT-48 | P8 | Web POST without the start-up token; request with `Host: evil.com` | 403; 400 |
| AT-49 | P9 | Tamper one `decisions` row | `audit verify` reports the decision-chain break; check chain intact |
| AT-50 | P9 | Reject `COLLECTOR` for X; reclassify; bump `classifier_version` | Suppressed, then returns |
| AT-51 | P9 | Confirm a deposit, name its entity, re-trace a target it funded | Deposit is a terminal at test 4 (no read) |
| AT-52 | P10 | Own wallet receives from a never-screened sender | Next `monitor run` screens it; REVIEW → exit 6 |
| AT-53 | P10 | Sender screened 2 days ago (`rescreen_days = 7`) | Skipped |
| AT-54 | P10 | Two `monitor run`s at once | Second exits "already running", screens nothing |
| AT-55 | P10 | `POST /v1/check` with Idempotency-Key, repeated; same key, other amount | Same `check_id`, no second record; 422 |
| AT-56 | P10 | `POST /v1/traces`, poll, repeat POST | 202 + id → `done`; repeat returns same id |
| AT-57 | P10 | API request without token / short token configured | 401 / server refuses to start |
| AT-58 | P11 | **live** Golden set | Targets in [roadmap P11](07-roadmap.md) met |
| AT-59 | P11 | **live** 3 TRON + 3 BSC traces, cold then warm | Within budgets; warm ≥ 50% fewer queries |
| AT-60 | P12 | History with a sanctioned sender (5% of inflow) and a frozen recipient (2% of outflow), both smaller than the 20 counterparties shown | Two direct exposures, `in` 0.05 and `out` 0.02, with exact volumes |
| AT-61 | P12 | Trace with a sanctioned terminal at hop 2 (weight 0.1) and a scam terminal at hop 3; decay 0.4, then 0; two sanctioned paths through one counterparty that sent less than both | Indirect exposures; contributions × 0.6 and × 0.36; with decay 0, × 1; volumes are path bottlenecks, capped together at that counterparty's edge (D-078) |
| AT-62 | P12 | Exposures giving X just below and above 30.5, 70.5 and 90.5; BLOCK; INCOMPLETE | Levels low/moderate, moderate/high, high/severe; 100 · severe; `≥ N · level+` |
| AT-63 | P12 | Only R-HEU-02 and R-TRC-04 fire, score 12; then score 31; then R-EXP-01 alone; then an override of R-HEU-02 to REVIEW | NO_HITS (findings INFO); REVIEW (R-SCR-01); REVIEW; REVIEW |
| AT-64 | P12 | `check --json`, `POST /v2/check`, a v1-era stored check, `POST /v1/check` | Contract-2 keys pinned; v1 record renders with score_version 1; 410 problem+json naming `/v2` |
| AT-65 | P12 | Check an OFAC address; an own wallet; an unknown personal wallet | `OFAC SDN: <name>`; the wallet's name; `PERSONAL (inferred, 0.5)` |
| AT-66 | P12 | A BSC transfer of 78951.063947843887500723 USDT | Shown `78,951.06` in text, web and PDF; exact in JSON |
| AT-67 | P13 | Trace fixtures with risk at hop 4 out and hop 5 in | Both found within budget; best-first order; partition sums to 1 per direction |
| AT-68 | P13 | History of 3 years, cap reached at 2 years; then the cap reached inside 180 days | Check complete with "older history not read before …"; INCOMPLETE |
| AT-69 | P14 | An address on the UK (or EU) list; a stale UK list; NBCTF never imported | R-SAN-01 BLOCK naming the list and entry; stale → INCOMPLETE, still BLOCK; NBCTF `skipped`, not required |
| AT-70 | P15 | **live** The owner's benchmark set | Level agreement reported; every mismatch has a reason; targets agreed with the owner met |
| AT-71 | P14 | NBCTF files with TRON and EVM addresses in any cell, one with a bad checksum | Valid addresses imported as an `nbctf` snapshot with the order id; the bad one reported, not imported; a listed address BLOCKs |
| AT-72 | P14 | Tracing in: a node that sent 30% of its outflow (≥ 1,000 USDT) to sanctioned or frozen addresses; one with 9%; one whose sender is sanctioned | First gives a `suspected_malicious` exposure (confidence 0.3, never BLOCK) and is traced through; second nothing; third: the sanctioned sender found as a fact |
| AT-73 | P14 | Tronscan tags (recorded answers) for a red-tagged and an exchange address | Red tag → `scam`-type label; exchange → named entity; only addresses the check reached are looked up |

