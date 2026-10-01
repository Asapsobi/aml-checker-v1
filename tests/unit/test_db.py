import sqlite3
from pathlib import Path

import pytest

from amlcheck.storage.db import (
    APPLICATION_ID,
    MigrationError,
    connect,
    load_migrations,
    migrate,
    open_db,
    schema_version,
)


def mig_dir(tmp_path: Path, files: dict[str, str]) -> Path:
    d = tmp_path / "migs"
    d.mkdir()
    for name, sql in files.items():
        (d / name).write_text(sql)
    return d


def test_open_db_uses_wal(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "sub" / "a.db")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_bundled_migrations_load() -> None:
    migs = load_migrations()
    assert [m.version for m in migs] == list(range(1, len(migs) + 1))


def test_open_db_reaches_latest_and_rerun_is_noop(tmp_path: Path) -> None:
    path = tmp_path / "a.db"
    conn = open_db(path)
    assert schema_version(conn) == len(load_migrations())
    assert migrate(conn) == []
    conn.close()
    conn = open_db(path)
    assert schema_version(conn) == len(load_migrations())


def test_migrates_empty_db_in_order(tmp_path: Path) -> None:
    d = mig_dir(
        tmp_path,
        {
            "0002_b.sql": "ALTER TABLE t ADD COLUMN b TEXT;",
            "0001_a.sql": "CREATE TABLE t (a INTEGER);",
            "README.txt": "ignored",
        },
    )
    conn = connect(tmp_path / "a.db")
    assert migrate(conn, load_migrations(d)) == ["0001_a.sql", "0002_b.sql"]
    assert schema_version(conn) == 2
    cols = [r[1] for r in conn.execute("PRAGMA table_info(t)")]
    assert cols == ["a", "b"]
    assert migrate(conn, load_migrations(d)) == []


def test_failed_migration_rolls_back(tmp_path: Path) -> None:
    d = mig_dir(
        tmp_path,
        {
            "0001_a.sql": "CREATE TABLE t (a INTEGER);",
            "0002_bad.sql": "CREATE TABLE u (x INTEGER); THIS IS NOT SQL;",
        },
    )
    conn = connect(tmp_path / "a.db")
    with pytest.raises(MigrationError, match=r"0002_bad\.sql failed"):
        migrate(conn, load_migrations(d))
    assert schema_version(conn) == 1
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables == {"t"}


@pytest.mark.parametrize(
    "files",
    [
        {"0001_a.sql": "", "0003_c.sql": ""},
        {"0001_a.sql": "", "0001_b.sql": ""},
        {"1_a.sql": ""},
        {"0001_Bad-Name.sql": ""},
    ],
)
def test_bad_migration_sets_refused(tmp_path: Path, files: dict[str, str]) -> None:
    with pytest.raises(MigrationError):
        load_migrations(mig_dir(tmp_path, files))


def test_newer_db_refused(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "a.db")
    conn.execute(f"PRAGMA application_id = {APPLICATION_ID}")
    conn.execute("PRAGMA user_version = 99")
    with pytest.raises(MigrationError, match="newer"):
        migrate(conn, [])


def test_new_db_is_marked_as_ours(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "a.db")
    assert conn.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID


@pytest.mark.parametrize(
    "setup",
    [
        # The older amlcheck's DB: tables, user_version 4, no application_id.
        "CREATE TABLE checks (id INTEGER); PRAGMA user_version = 4;",
        "CREATE TABLE anything (x TEXT);",
        "PRAGMA application_id = 12345;",
    ],
)
def test_foreign_db_refused_untouched(tmp_path: Path, setup: str) -> None:
    path = tmp_path / "a.db"
    raw = sqlite3.connect(path)
    raw.executescript(setup)
    before = raw.execute("PRAGMA user_version").fetchone()[0]
    raw.close()
    with pytest.raises(MigrationError, match="not created by this amlcheck"):
        open_db(path)
    raw = sqlite3.connect(path)
    assert raw.execute("PRAGMA user_version").fetchone()[0] == before
    assert raw.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
    assert raw.execute("PRAGMA journal_mode").fetchone()[0] == "delete"


def test_0001_cache_schema(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "a.db")
    assert schema_version(conn) >= 1
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"transfers", "history_windows", "contracts"} <= tables
    assert "http_cache" not in tables  # D-037

    def pk(table: str) -> list[str]:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return [r[1] for r in sorted((r for r in rows if r[5]), key=lambda r: r[5])]

    # D-030: the TRON transfer key needs sender, recipient and amount.
    assert pk("transfers") == ["chain", "tx_hash", "sender", "recipient", "amount", "idx"]
    assert pk("history_windows") == ["chain", "address_norm", "since"]
    assert pk("contracts") == ["chain", "address_norm"]
    indexes = {r[1] for r in conn.execute("PRAGMA index_list(transfers)")}
    assert {"transfers_in", "transfers_out"} <= indexes


def test_transaction_commits_or_rolls_back(tmp_path: Path) -> None:
    from amlcheck.storage.db import transaction

    conn = open_db(tmp_path / "a.db")
    with transaction(conn):
        conn.execute("INSERT INTO contracts VALUES ('bsc', '0x1', 1, 't', 's')")

    def failing_write() -> None:
        with transaction(conn):
            conn.execute("INSERT INTO contracts VALUES ('bsc', '0x2', 1, 't', 's')")
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        failing_write()
    assert [r[0] for r in conn.execute("SELECT address_norm FROM contracts")] == ["0x1"]


def test_p0_database_upgrades_to_p1(tmp_path: Path) -> None:
    # A DB created by P0: marked as ours, schema 0, no tables.
    path = tmp_path / "a.db"
    raw = sqlite3.connect(path)
    raw.execute(f"PRAGMA application_id = {APPLICATION_ID}")
    raw.close()
    conn = open_db(path)
    assert schema_version(conn) == len(load_migrations())
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "transfers" in tables


def test_0002_screening_schema(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "a.db")
    assert schema_version(conn) >= 2
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "list_snapshots",
        "sanctioned_addresses",
        "issuer_events",
        "index_state",
        "checks",
        "check_sources",
        "check_findings",
    } <= tables
    cols = [r[1] for r in conn.execute("PRAGMA table_info(checks)")]
    assert cols[:2] == ["seq", "check_id"]
    assert {"prev_hash", "record_hash", "config_hash", "rules_version", "tool_version"} <= set(cols)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO checks (check_id, created_at, chain, address_norm, verdict, tool_version, "
            "rules_version, config_hash, prev_hash, record_hash) "
            "VALUES ('c1', 't', 'tron', 'T', 'MAYBE', 'v', 1, 'h', 'p', 'r')"
        )
