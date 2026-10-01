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
