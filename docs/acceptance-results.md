# amlcheck — Acceptance results

> Which tests prove each acceptance test ([06-acceptance-tests.md](06-acceptance-tests.md)), and live
> runs per phase. Updated at every phase review.

## P0 · Foundations & verification

| AT | Tests | Status |
|---|---|---|
| AT-01 | `tests/unit/test_cli.py::test_version`, `::test_status_on_empty_home`, `::test_status_json` | Green locally and in CI (5 jobs, 2026-10-01) |
| AT-02 | `tests/unit/test_config.py::test_bad_config_refused` (13 cases), `tests/unit/test_cli.py::test_status_refuses_bad_config` | Green locally and in CI (5 jobs, 2026-10-01) |

**Live (2026-10-01, macOS, scratch `AMLCHECK_HOME`):** `amlcheck --version` → `amlcheck 0.0.0`;
`amlcheck status` on an empty home created the DB at schema 0 (latest 0, D-027); a config with
`lookbak_days` exited 1 with `[exposure] lookbak_days: unknown key`.

**Also covered in P0:** a DB this amlcheck didn't create is refused untouched (D-035):
`tests/unit/test_db.py::test_foreign_db_refused_untouched`, `tests/unit/test_cli.py::test_status_refuses_foreign_db`.

## P1 · Chain data layer

| AT | Tests | Status |
|---|---|---|
| AT-03 | `tests/unit/test_cache.py::test_at03_second_read_asks_only_the_gap`, `::test_d028_recent_tail_is_not_refetched`, `::test_property_every_read_is_exact` | Green locally (2026-10-01) |
| AT-04 | `tests/unit/test_cache.py::test_at04_limit_marks_incomplete_and_stays_honest`; at the sources `test_tron.py::test_limit_marks_incomplete`, `test_bsc.py::test_busy_wallet_newest_limit_incomplete` | Green locally |
| AT-05 | `tests/unit/test_cache.py::test_at05_zero_value_counted`; `test_tron.py::test_zero_value_dropped_and_counted`, `test_bsc.py::test_zero_value_dropped_and_counted` | Green locally |
| AT-06 | `tests/unit/test_limits.py::test_at06_429_with_budget_headers_waits_then_succeeds`, `::test_at06_wait_over_65_s_is_error` | Green locally |
| AT-14 (P2, network part built here) | `tests/unit/test_http.py::test_retry_after_short_is_waited_once`, `::test_retry_after_long_is_error` | Green locally |

Property tests: `test_bsc.py::test_property_matches_brute_force` (newest-first reading on an
oldest-first indexer, block bounds across slow and fast blocks) and
`test_cache.py::test_property_every_read_is_exact` (random read sequences against brute force).

**Live (2026-10-01, macOS, owner's TronGrid key and HyperSync token, scratch `AMLCHECK_HOME`,
`amlcheck history <addr> --json`, defaults: 180 days, 5,000 transfers):**

| Chain | Address | Cold | Warm (cache) | Transfers | Complete |
|---|---|---|---|---|---|
| TRON | quiet `TNHrhtVn…vXJJaa` | 1.5 s | 0.3 s | 233 | yes |
| TRON | busy `TN12qS4g…aDGdRY` | 13.9–15.6 s (25 pages) | 0.3 s | 5,000 (all from today) | no, as expected |
| BSC | quiet `0x563964…a67ca6` | 5.2 s | 0.3 s | 221 | yes |
| BSC | busy `0x8894e0…e2d4e3` | 6.4–8.1 s | 0.3 s | 5,000 (all from today) | no, as expected |

First activity (`--first-activity`): BSC quiet wallet 2025-06-06T12:58:05Z in 11.0 s (scan from block
0); TRON quiet wallet 2026-09-10T04:20:00Z in 1.1 s. `cache stats` after the run: 2 addresses and
~5,200 transfers per chain, 6.3 MB.

## P2 · Screening MVP

| AT | Tests | Status |
|---|---|---|
| AT-07 | `test_cli_check.py::test_at07_invalid_address`; `test_address.py::test_invalid_refused` | Green locally (2026-10-03) |
| AT-08 | `test_cli_check.py::test_at08_bad_checksum`; `test_address.py::test_bad_eip55_refused` | Green locally |
| AT-09 | `test_cli_check.py::test_at09_clean_check`; `test_engine.py::test_at09_clean_is_no_hits_and_recorded` | Green locally |
| AT-10 | `test_sanctions.py::test_at10_listed_under_any_label_blocks` (TRON under XBT; `0x` under ARB/BSC/ETH; ETH label matched on BSC) | Green locally |
| AT-11, AT-12 | Dropped with the EVM freeze index (D-039) | — |
| AT-13 | `test_tron_freeze.py::test_at13_lag_after_failed_refresh` (59 min ok, 61 min stale) | Green locally |
| AT-14 | `test_http.py::test_retry_after_short_is_waited_once`, `::test_retry_after_long_is_error` (built in P1) | Green locally |
| AT-15 | `test_sanctions.py::test_at15_stale_after_48h_but_listing_still_reported` | Green locally |
| AT-16 | `test_engine.py::test_at16_block_wins_over_a_gap`; `test_rules_verdict.py::test_precedence` | Green locally |
| AT-17 | `test_tron_freeze.py::test_at17_rules_from_index` (added, added→removed, re-added, destroyed) | Green locally |
| AT-18 | `test_tron_freeze.py::test_at18_deprecated_contract_is_error` | Green locally |
| AT-19 | `test_bsc_freeze.py::test_at19_always_skipped_with_reason`; `test_cli_check.py::test_bsc_check_with_real_sources` | Green locally |
| AT-20 | `test_audit.py::test_at20_tamper_reported_at_that_record` (6 kinds of tampering), `::test_deleted_record_is_reported`; `test_cli_check.py::test_audit_list_and_verify` | Green locally |
| AT-21 | `test_sanctions.py::test_at21_shrunk_list_rejected` | Green locally |

**Live (2026-10-03, macOS, owner's TronGrid key, scratch `AMLCHECK_HOME`, a slow line of 12–130 KB/s):**

`amlcheck sync`: first attempt with the 29 MB `SDN.XML` failed after 35 min (stalls, each retry from
zero), which led to D-040. With the zipped list and resumable download: **385 s**, 1,066 addresses
(list of 2026-10-02); the TRON freeze index's first sync took ~64 s (10,923 events), later refreshes
~3 s.

| Chain | Address | Expected | Verdict (exit) | Findings | Time |
|---|---|---|---|---|---|
| TRON | OFAC-listed `TA3941uF…oa86mz` (USDT label) | BLOCK | **BLOCK (5)** | R-SAN-01 (CHEIL CREDIT BANK, DPRK4) and R-FRZ-01 twice (index + `isBlackListed`) | 2.9 s |
| TRON | OFAC-listed `TUCsTq7T…m5Yzq` (filed under **XBT**) | BLOCK | **BLOCK (5)** | R-SAN-01 (entry 45404) and R-FRZ-01 twice | 5.2 s |
| TRON | Tether-frozen `TNHrhtVn…vXJJaa` | BLOCK | **BLOCK (5)** | R-FRZ-01 (index, since 2026-10-01) and `isBlackListed` | 3.2 s |
| TRON | never used (random) | NO_HITS | **NO_HITS (0)** | — | 8.1 s |
| BSC | OFAC-listed `0x4f47bc…96270c` (ARB/BSC/ETH labels) | BLOCK | **BLOCK (5)** | R-SAN-01 (Hyon Sop SIM, NPWMD); BEP20 freeze `skipped` with reason | ~0.5 s |
| BSC | never used (random) | NO_HITS | **NO_HITS (0)** | — ; BEP20 freeze `skipped` | 0.3 s |

A mixed-case `0x` address typed with a broken checksum was refused (exit 1) and wrote no record
(AT-07/AT-08 live). `amlcheck audit verify`: OK, 6 records. `amlcheck status`: list 0 h old, TRON index
1 min behind.

## P3 · Exposure & behaviour

| AT | Tests | Status |
|---|---|---|
| AT-22 | `test_exposure.py::test_at22_frozen_counterparty` | Green locally (2026-10-03) |
| AT-23 | `test_exposure.py::test_at23_flagged_inflow_share` (6% raised, 4% not) | Green locally |
| AT-24 | `test_exposure.py::test_at24_pass_through_and_allowlist` | Green locally |
| AT-25 | `test_exposure.py::test_at25_fan_in` (60 × 20 USDT in 3 h raised; ≥ 100 USDT not) | Green locally |
| AT-26 | `test_exposure.py::test_at26_too_many_transfers_is_stale` | Green locally |
| AT-27 | `test_labels.py::test_at27_bad_row_imports_nothing` | Green locally |

Also: `test_heuristics.py` (FIFO edge cases, window boundaries, property test), allowlist never cancels
R-EXP-01, caps of 10 largest first, R-HEU-01 new/unused, the readable output snapshot.
**Performance (T-3.07):** 1,000-transfer check offline p95 15 ms, median 8 ms (budget 60 s).

**Live (2026-10-03, owner's keys, scratch `AMLCHECK_HOME`, lists synced the same day):**

| Chain | Address | Verdict (exit) | Rules | Exposure | Time |
|---|---|---|---|---|---|
| TRON | counterparty of the Tether-frozen address, `TF1gUASc…2BQK` | **REVIEW (3)** | R-EXP-01, R-HEU-02 | ok: 881 transfers, 216 counterparties | 16.6 s |
| TRON | Tether-frozen `TNHrhtVn…vXJJaa` | **BLOCK (5)** | R-FRZ-01, R-HEU-02 | ok: 233 transfers, 60 counterparties | 25.4 s |
| TRON | busy `TN12qS4g…aDGdRY` | **INCOMPLETE (4)** | R-SYS-01, R-HEU-03, R-HEU-04 | stale: > 5,000 transfers (D-013) | 41.3 s |
| BSC | quiet `0x563964…a67ca6` | **REVIEW (3)** | R-HEU-02 | ok: 223 transfers, 17 counterparties | 16.9 s |
| BSC | busy `0x8894e0…e2d4e3` | **INCOMPLETE (4)** | R-SYS-01, R-HEU-02/03/04 | stale: > 5,000 transfers | 34.1 s |

`audit verify`: OK, 11 records. Note for P11 calibration: R-HEU-02 (pass-through) fired on 4 of 5
live addresses; deposit and trading wallets move money on within a day, so this rule is noisy at the
methodology's defaults.

## P4 · Intelligence store

| AT | Tests | Status |
|---|---|---|
| AT-28 | `test_intel_p4.py::test_at28_rebuild_gives_identical_rows` (10 checks over 4 addresses); `test_cli_intel.py::test_cp_after_checks_and_rebuild` | Green locally (2026-10-03) |
| AT-29 | `test_intel_p4.py::test_at29_lookalike_of_a_counterparty`, `::test_lookalike_of_a_trusted_label_and_not_of_itself` | Green locally |
| AT-30 | `test_intel_p4.py::test_at30_pack_without_licence_refused`; `test_cli_intel.py::test_import_pack_needs_licence` | Green locally |
| AT-31 | `test_store.py::test_at31_add_then_retract`; `test_cli_intel.py::test_label_retract_show` | Green locally |

Also: category table (§8) and provenance rules, `best_terminal()` ordering, entities, packs re-import,
exposure reading intel labels, `cache prune` keep rule (D-043).

**Live (2026-10-03, owner's keys, scratch `AMLCHECK_HOME` from P2/P3):**

- `amlcheck cp rebuild` on the 11 audit records written before the registry existed: 10 counterparties.
- **Real address poisoning found in the cache:** 7 groups of look-alike addresses among 8,479 cached
  addresses, each a real counterparty (millions of USDT) next to a look-alike that sent one dust
  transfer (~1 USDT), e.g. `TSpfocMSWfjWF1zHYXtusawoB9cPt2FtwD` (21 transfers, 5.1M USDT) and
  `TSpfopdGbCwmM46iFF9q6ALXiD6rt2FtwD` (one transfer of 1.0001 USDT).
- After checking the real one (client `corridor`), `amlcheck check TSpfopdG…2FtwD` → **REVIEW,
  R-HEU-06**: "Looks like a known counterparty but isn't: TSpfo[pdGbCwmM46iFF9q6ALXiD6r]t2FtwD vs
  TSpfo[cMSWfjWF1zHYXtusawoB9cP]t2FtwD". `cp show` lists it as LOOKS LIKE the real one.
- `audit verify`: OK, 13 records.

## P5 · Profiler & classifier

| AT | Tests | Status |
|---|---|---|
| AT-32 | `test_classifier.py::test_at32_hub` (600 counterparties → 0.7; capped → 0.95); `test_profiler.py::test_hub_is_capped` | Green locally (2026-10-03) |
| AT-33 | `test_profiler.py::test_at33_deposit_linked_and_named`; `test_cli_classify.py::test_classify_and_entities` | Green locally |
| AT-34 | `test_classifier.py::test_at34_collector`; `test_profiler.py::test_at34_collector_raises_heu07` | Green locally |
| AT-35 | `test_config.py::test_bad_config_refused[R-HEU-07 BLOCK]` (since P0) | Green locally |
| AT-36 | `test_classifier.py::test_at36_boundaries` (25 cases: every condition at and just past its threshold) | Green locally |

Also: every §5 feature (`test_features.py`), contract lookups on real VS-12/VS-13 answers, cached
classification with 14-day expiry, operator label beats inference, operator membership never
overwritten, the top recipient's classification reused.

**Live (2026-10-03, owner's keys, scratch `AMLCHECK_HOME`, `amlcheck classify <addr> --json`):**

| Chain | Address | Role | Result | Time |
|---|---|---|---|---|
| BSC | `0x8894e0…e2d4e3` (busy exchange hot wallet) | hub | **HUB 0.95** (capped; 830 senders, 161 recipients in the read) | 36 s |
| BSC | `0xb7f58a…e3bed6` (sends to it) | deposit | **DEPOSIT 0.9 primary**, FRESH; linked to `hub-0x8894e0` | 12 s |
| BSC | `0x3fc37b…0812d` (sends to it) | busy deposit | HUB 0.95 primary (its own read is capped) **and DEPOSIT 0.9** | 30 s |
| TRON | `TN12qS4g…aDGdRY` (busy wallet) | hub | **HUB 0.95** (capped; 751 senders) | 7 s |
| TRON | `TAyDpm2w…Di9E9P` (sent it 45 transfers) | deposit | **DEPOSIT 0.9 primary**, FRESH; linked to `hub-TN12qS4g` | 5 s |

Each DEPOSIT held all six conditions plus three bonuses (≥ 5 senders, median hold ≤ 12 h, ≥ 99% to the
hub). `amlcheck check 0xb7f58a…` (8 s): the classifier source reports "DEPOSIT (0.9), also FRESH";
R-HEU-01 (new) and R-HEU-02 (pass-through) fire. `audit verify`: OK, 14 records.

Notes for P11 calibration: a very busy deposit address is capped and so primary HUB (table order);
R-HEU-02 fires on deposit addresses by nature (they sweep within hours).

## P6 · Source-of-funds trace

| AT | Tests | Status |
|---|---|---|
| AT-37 | `test_trace_engine.py::test_at37_worked_example` (partition, coverage 0.9, 3 reads, D-047); `test_trace_rules.py::test_at37_findings`; through a check: `test_trace_jobs.py::test_trace_source_in_a_check` (REVIEW, trace id in the audit record) | Green locally (2026-10-03) |
| AT-38 | `test_trace_property.py::test_at38_random_graphs` (500 random graphs: partition = 1 ± 0.001, reads ≤ `max_nodes`, identical JSON on re-run) | Green locally |
| AT-39 | `test_trace_engine.py::test_at39_read_failure_keeps_partial`; `test_trace_jobs.py::test_trace_source_failure_is_incomplete` (INCOMPLETE, R-SYS-01 naming the node and hop, partial saved with the job); `test_cli_trace.py::test_trace_incomplete`, `test_investigate_incomplete` | Green locally |
| AT-40 | `test_trace_engine.py::test_at40_node_budget` (`untraced:budget` 0.1, complete) | Green locally |
| AT-41 | `test_trace_engine.py::test_at41_cycle` (`untraced:cycle`, terminates) | Green locally |

Also: the job queue (atomic claim, idempotency key, an abandoned `running` job resumed after the time
budget + 120 s, progress, failures stored with the partial); the time budget bounding a read in
progress; `check --trace/--no-trace` and the automatic trace at ≥ 10,000 USDT; the trace's own
background client (D-036); the SVG graph (snapshot of the §7.11 example, 8 + 6 labels, edge widths);
`trace` and `investigate` (D-049); the BSC read that no longer rescans empty blocks.

**Live budget run, VS-07 (2026-10-03, owner's keys, scratch copies of the database emptied of all
cached chain data per target):** see [verification-log.md](verification-log.md#vs-07--trace-budget-cold-and-warm-prd-g7-g8).

| | TRON (3 traces) | BSC (3 traces) | Target |
|---|---|---|---|
| Cold: requests | 43, 105, 86 TronGrid | 9, 31, 35 HyperSync | ≤ 200 / ≤ 120 (G8) |
| Cold: time | 28–71 s | 63–147 s | ≤ 5 min on BSC |
| Warm: requests | 1 each | 2 each | ≥ 50% fewer (G7): 78–99% |
| Warm: time | ≤ 0.8 s | ≤ 3.1 s | ≤ 90 s |

The run found two bugs, both fixed before these numbers: the BSC backward read rescanned empty blocks
for new busy wallets (181 queries and 632 s → 31 queries and 63 s on the same trace), and the time
budget did not bound a read in progress.

`amlcheck investigate TVvWhZyL…LeSsWP --amount 20000` (scratch copy, warm): **REVIEW**. R-TRC-01: a
sanctioned wallet 2 hops away, every hop on the path moved ≥ 34,733 USDT (estimated share 0.7%);
R-TRC-04: coverage 29.4%. The check's audit record carries the trace id; `audit verify`: OK, 15 records.
`amlcheck trace … --svg` drew the 57-item graph.

Notes for P11 calibration: coverage was 6.6–44% on four of six real targets, mostly
`untraced:pruned` (5 senders kept per node) and `untraced:depth`; consider `branch` and `coverage_share`
against the golden set.

## P7 · Score & reports

| AT | Tests | Status |
|---|---|---|
| AT-42 | `test_score.py::test_at42_worked_example` (H 0.24 → 66 `high`, E 59.5, D 0, B 5, U 1); through a check: `test_score_check.py::test_check_carries_its_score` | Green locally (2026-10-03) |
| AT-43 | `test_score.py::test_at43_block_and_incomplete` (BLOCK → 100; INCOMPLETE with E 34 → `≥ 34 · medium+`); `test_score_check.py::test_block_and_incomplete_through_a_check` | Green locally |
| AT-44 | `test_score_check.py::test_at44_old_and_new_records_verify` (a record without `score_json` and one with it verify; changing a stored score breaks the chain) | Green locally |

Also: band edges, the 99 cap, caps of `D` and `B`, a rule counted once, rounding half up with the stored
breakdown reproducing the score (D-052), inferred terminals at their own confidence, the registry's
`last_score` (also after `cp rebuild`), R-SCR-01 off by default and REVIEW-only when on (D-051, refused
as BLOCK at config load), the score in `check`/`investigate`/`audit list`/`cp list`/`cp show` and JSON
(snapshot tests), and the case report: every section, same bytes for the same records, a choice of
check, refusals, a pre-P7 record, a tampered record flagged, text outside WinAnsi (D-053).

**Live (2026-10-03, owner's keys, the VS-07 scratch copies of the database):**

| Chain | Target | Command | Result |
|---|---|---|---|
| TRON | `TVvWhZyL…LeSsWP` | `investigate --amount 20000` | REVIEW, **38 · medium** (E 30.8 · D 0 · B 0 · U 7.1; H 0.036): R-TRC-01, R-TRC-04 |
| BSC | `0x0c1e52…ee1576` | `check --amount 15000` (trace on by amount) | REVIEW, **36 · medium** (E 12.9 · D 0 · B 15 · U 8.4): R-HEU-01, R-HEU-02, R-TRC-04 |

`amlcheck cp report` on both: 3-page PDFs with every section, the TRON one with its 57-box trace graph on its
own page, the record's hash matching its contents. `audit list` shows the score next to the verdict
(`-` for records from before P7); `audit verify`: OK on both databases (16 and 15 records).

Notes for P11 calibration: on the TRON target most of `E` comes from money through unattributed
services and contracts (weight 0.1 each: H 0.028 of 0.036), not from the 0.8% sanctioned share. Labelling
the exchanges behind them (weight 0) would lower it; check the weights against the golden set.

## P8 · Operator UX

| AT | Tests | Status |
|---|---|---|
| AT-45 | `test_cli_batch.py::test_at45_one_bad_row_screens_nothing` (100 rows, one invalid: nothing screened, exit 1, the line named); `test_at45_fixed_file_all_screened` (all 100 screened in order, results streamed, exit = most serious verdict) | Green locally (2026-10-03) |
| AT-46 | `test_cli_watch.py::test_at46_changed_verdict` (first run is not a change; a changed verdict is listed, notified, recorded, exit 6) | Green locally |
| AT-47 | `test_export.py::test_at47_csv_export_is_spreadsheet_safe` (`=…` and `+…` cells prefixed with `'`); `test_csv_cell` | Green locally |
| AT-48 | `test_web.py::test_at48_host_allow_list` (`Host: evil.com` → 400); `test_at48_token` (no cookie / wrong token / POST without the form token → 403) | Green locally |

Also: batch header and value refusals, blank lines and file line numbers, the trace by amount, the run
lock shared by batch and watch; watch add/remove/list, the webhook payload and a failing webhook that
only warns, the notification message passed as an argument (no AppleScript injection), webhook URLs
limited to http(s); export filters, the JSON export re-verifying the hash chain, the PDF export; web
security headers and no scripts, the check flow, bad input recording nothing, history and
counterparties with escaping of hostile text, traces from the web with progress (D-054 to D-057).

**Live (2026-10-03, owner's keys, the VS-07 scratch copies of the database):**

| What | Result |
|---|---|
| `batch` with an invalid 4th row | Refused: "line 5: not a TRON … or EVM … address"; nothing screened (15 records before and after), exit 1 |
| `batch` of 3 real addresses | NO_HITS `TVvWhZyL…LeSsWP`; REVIEW 15 · low `0x0c1e52…ee1576`; **BLOCK 100 · severe** `TA3941uF…X86mz` (OFAC-listed and Tether-frozen); exit 5; results CSV streamed |
| `watch add` ×2, `watch run` ×2 | First run "new", second "same"; exit 0 both times |
| `audit export` csv, json, pdf | 22 records each; an independent script recomputed every hash from the JSON file alone and the links hold; `audit verify` OK |
| `amlcheck web` (scratch copy, port 8799) | Sign-in, check form with recent checks, check detail (score, findings, sources, PDF link), the 57-box trace page, counterparties, the look-alike warning on a poisoned address; no console errors (CSP) |

## P9 · Cases & decisions

| AT | Tests | Status |
|---|---|---|
| AT-49 | `test_cases.py::test_at49_tampered_decision` (decision chain broken at #1, check chain verifies); `test_cli_case.py::test_at49_audit_verify_reports_the_decision_chain` (`audit verify` names the decision log, exit 1) | Green locally (2026-10-04) |
| AT-50 | `test_cases.py::test_at50_rejected_type_suppressed_until_version_changes` (reject COLLECTOR → stored classification stale, reclassification without it; classifier version 2 → it returns) | Green locally |
| AT-51 | `test_cases.py::test_at51_confirmed_deposit_resolves_locally` (before: `service_unattributed` at test 5; after confirming the DEPOSIT and naming its entity: `exchange_regulated` at test 4, no read) | Green locally |

Also: case open rules (REVIEW/BLOCK/INCOMPLETE only, one open case per address, a name required,
D-058, D-060), decide (note required, `escalated` keeps the case open, `approved`/`rejected` close it,
closed cases refuse more), each decision tied to its check's record hash and a changed check record
caught, confirm per type (DEPOSIT membership, HUB naming, label with a chosen category; inferred-only
categories refused, D-059), a rejected DEPOSIT unlinked from its hub, `case export --jsonl` (D-061),
decisions in CSV/JSON/PDF exports and in the case report, web case pages (open, decide, reject).

**Live (2026-10-04, the VS-07 BSC scratch copy, operator `live-test`):** a case on the REVIEW check of
`0x0c1e52…ee1576` (types PASS_THROUGH, FRESH); escalated, PASS_THROUGH confirmed as `high_risk`
(operator label #1, now the decisive category), then rejected and closed. `audit verify`: audit log
OK (22 records), decision log OK (2 decisions). `case export --jsonl`: 2 lines with the check's
verdict, rules, score and feedback. `audit export`: both decisions on check #22. `cp report`: the
Decision section lists both with the decision chain head.

