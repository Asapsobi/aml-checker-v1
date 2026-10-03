import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from amlcheck.core.audit import (
    GENESIS,
    AuditFinding,
    AuditRecord,
    AuditSource,
    append,
    canonical_json,
    load,
    record_hash,
    verify,
)
from amlcheck.storage.db import open_db


def rec(n: int, **extra: object) -> AuditRecord:
    return AuditRecord(
        check_id=f"chk-{n}",
        created_at=f"2026-10-01T10:00:{n:02d}.000000Z",
        chain="tron",
        address_norm="TNHrhtVnRMRaTJFRsqLUqSytCKsavXJJaa",
        verdict="BLOCK" if n % 2 else "NO_HITS",
        tool_version="0.2.0",
        rules_version=1,
        config_hash="ab" * 32,
        sources=(
            AuditSource("ofac_sdn", True, "ok", "not listed", {"snapshot": "s1"}, "2026-10-01"),
            AuditSource("tron_freeze", True, "ok", "frozen", {"amount": Decimal("12.5")}),
        ),
        findings=(
            AuditFinding(
                "R-FRZ-01",
                "BLOCK",
                "tron_freeze",
                "frozen by Tether",
                "2026-10-01T10:00:00Z",
                {"tx": "0xabc", "priority": "low"},
            ),
        ),
        **extra,  # type: ignore[arg-type]
    )


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / "a.db")


def test_canonical_json_is_stable() -> None:
    assert canonical_json({"b": 1, "a": [Decimal("1.50"), "é"]}) == '{"a":["1.50","é"],"b":1}'
    with pytest.raises(TypeError, match="float"):
        canonical_json({"x": 1.5})


def test_chain_links_and_verifies(conn: sqlite3.Connection) -> None:
    a1 = append(conn, rec(1))
    a2 = append(conn, rec(2, amount="50000", client="ACME"))
    assert a1.prev_hash == GENESIS
    assert a2.prev_hash == a1.record_hash
    v = verify(conn)
    assert v.ok
    assert v.records == 2
    assert v.head_hash == a2.record_hash


def test_round_trip_through_database(conn: sqlite3.Connection) -> None:
    r = rec(3, operator_note="pre-trade")
    a = append(conn, r)
    loaded = load(conn, a.seq)
    assert loaded is not None
    again, prev, stored = loaded
    assert record_hash(prev, again) == stored == a.record_hash


def test_floats_in_evidence_refused(conn: sqlite3.Connection) -> None:
    bad = AuditRecord(
        "c",
        "t",
        "tron",
        "T",
        "NO_HITS",
        "v",
        1,
        "h",
        sources=(AuditSource("x", True, "ok", "s", {"share": 0.1}),),
    )
    with pytest.raises(TypeError, match="float"):
        append(conn, bad)
    assert conn.execute("SELECT count(*) FROM checks").fetchone()[0] == 0  # rolled back


# F6.4: a nullable field added later is hashed only when set, so old records keep verifying.
def test_unset_optional_fields_do_not_change_the_hash() -> None:
    plain = rec(1)
    assert "score_json" not in plain.body()["check"]
    assert record_hash(GENESIS, plain) == record_hash(GENESIS, rec(1, score_json=None))
    assert record_hash(GENESIS, plain) != record_hash(GENESIS, rec(1, score_json='{"score":5}'))


def test_order_of_sources_and_findings_does_not_matter() -> None:
    r = rec(1)
    flipped = AuditRecord(**{**r.__dict__, "sources": tuple(reversed(r.sources))})
    assert record_hash(GENESIS, r) == record_hash(GENESIS, flipped)


# AT-20: tamper one checks row → `audit verify` reports the break at that record.
@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        ("UPDATE checks SET verdict = 'REVIEW' WHERE seq = 2", "content changed"),
        ("UPDATE checks SET amount = '1' WHERE seq = 2", "content changed"),
        (
            "UPDATE check_findings SET severity = 'REVIEW' WHERE check_id = 'chk-2'",
            "content changed",
        ),
        ("UPDATE check_sources SET status = 'stale' WHERE check_id = 'chk-2'", "content changed"),
        ("DELETE FROM check_findings WHERE check_id = 'chk-2'", "content changed"),
        ("UPDATE checks SET prev_hash = 'x' WHERE seq = 2", "link"),
    ],
)
def test_at20_tamper_reported_at_that_record(
    conn: sqlite3.Connection, sql: str, reason: str
) -> None:
    for n in (1, 2, 3):
        append(conn, rec(n))
    conn.execute(sql)
    v = verify(conn)
    assert not v.ok
    assert v.break_at == 2
    assert reason in (v.reason or "")
    assert v.records == 1


def test_deleted_record_is_reported(conn: sqlite3.Connection) -> None:
    for n in (1, 2, 3):
        append(conn, rec(n))
    conn.execute("PRAGMA foreign_keys = OFF")  # as from the sqlite shell
    conn.execute("DELETE FROM checks WHERE seq = 2")
    v = verify(conn)
    assert not v.ok
    assert v.break_at == 3


def test_deleted_first_record_is_reported(conn: sqlite3.Connection) -> None:
    for n in (1, 2):
        append(conn, rec(n))
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DELETE FROM checks WHERE seq = 1")
    v = verify(conn)
    assert not v.ok
    assert v.break_at == 2
    assert "link" in (v.reason or "")


def test_empty_log_verifies(conn: sqlite3.Connection) -> None:
    v = verify(conn)
    assert v.ok
    assert v.records == 0
    assert v.head_hash == GENESIS
