import sqlite3
from pathlib import Path

import pytest

from amlcheck.storage.db import (
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


def test_connect_uses_wal(tmp_path: Path) -> None:
    conn = connect(tmp_path / "sub" / "a.db")
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
    conn.execute("PRAGMA user_version = 99")
    with pytest.raises(MigrationError, match="newer"):
        migrate(conn, [])
