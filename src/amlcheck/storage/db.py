"""SQLite connection (WAL) and the `user_version` migration runner.

Migrations are `storage/migrations/NNNN_<name>.sql`, numbered from 0001 with no gaps, and
append-only once released (data model intro). Each is applied in its own transaction together with
its `user_version` bump, so a failed migration leaves the DB at the previous version.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")

#: Marks a DB created by this amlcheck (D-035). An older tool of the same name keeps its own DB in
#: `~/.amlcheck/` with `user_version` 4; without the mark, P1–P4 would read it as our schema 4.
APPLICATION_ID = 0x616D6C63  # "amlc"


class MigrationError(Exception):
    pass


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


def connect(path: Path) -> sqlite3.Connection:
    """Open in autocommit mode with foreign keys and a busy timeout (architecture §5).

    Nothing persistent is changed here: `migrate()` switches on WAL only once the DB is known to be
    ours (D-035), so a refused DB is left exactly as it was.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, timeout=30.0)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def load_migrations(source: Traversable | Path | None = None) -> list[Migration]:
    """Read migration files in order. Gaps, duplicates and badly named `.sql` files are errors."""
    root = source if source is not None else resources.files("amlcheck.storage.migrations")
    found: list[Migration] = []
    for entry in root.iterdir():
        if not entry.name.endswith(".sql"):
            continue
        m = _NAME.match(entry.name)
        if not m:
            raise MigrationError(f"bad migration file name: {entry.name}")
        found.append(Migration(int(m.group(1)), entry.name, entry.read_text(encoding="utf-8")))
    found.sort(key=lambda mig: (mig.version, mig.name))
    for expected, mig in enumerate(found, start=1):
        if mig.version != expected:
            raise MigrationError(f"migrations must be numbered 0001.. without gaps: {mig.name}")
    return found


def schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0])


def _claim(conn: sqlite3.Connection) -> None:
    """Mark an empty DB as ours; refuse any DB this amlcheck did not create."""
    app_id = int(conn.execute("PRAGMA application_id").fetchone()[0])
    if app_id == APPLICATION_ID:
        return
    empty = conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == 0
    if app_id == 0 and empty and schema_version(conn) == 0:
        conn.execute(f"PRAGMA application_id = {APPLICATION_ID}")
        return
    raise MigrationError(
        "this database was not created by this amlcheck (it may belong to an older amlcheck); "
        "point AMLCHECK_HOME at another folder or move the old one away"
    )


def migrate(conn: sqlite3.Connection, migrations: list[Migration] | None = None) -> list[str]:
    """Apply missing migrations. Returns the names applied (empty when already up to date)."""
    migrations = load_migrations() if migrations is None else migrations
    _claim(conn)
    conn.execute("PRAGMA journal_mode=WAL")
    latest = len(migrations)
    current = schema_version(conn)
    if current > latest:
        raise MigrationError(
            f"database schema {current} is newer than this amlcheck knows ({latest}); "
            "upgrade amlcheck"
        )
    applied: list[str] = []
    for mig in migrations[current:]:
        # executescript commits any open transaction first, so BEGIN/COMMIT go inside the script.
        script = f"BEGIN;\n{mig.sql}\n;PRAGMA user_version = {mig.version};\nCOMMIT;"
        try:
            conn.executescript(script)
        except sqlite3.Error as e:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise MigrationError(f"{mig.name} failed: {e}") from e
        applied.append(mig.name)
    return applied


def open_db(path: Path) -> sqlite3.Connection:
    """Connect and bring the schema up to date."""
    conn = connect(path)
    try:
        migrate(conn)
    except BaseException:
        conn.close()
        raise
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """One short write transaction (architecture §5): commit on success, roll back on any error."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
