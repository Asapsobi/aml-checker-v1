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

