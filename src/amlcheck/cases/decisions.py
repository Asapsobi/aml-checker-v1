"""The decision chain: append-only, hash-chained like the audit log (PRD F12.2, D-060).

- Each decision's hash is sha256(prev_hash + canonical JSON of its fields), the same construction as
  the check chain (`core.audit`), starting from the same genesis.
- `check_record_hash` ties a decision to the exact check record the operator looked at;
  verification also checks that this record still has that hash, so the evidence behind a decision
  can't be swapped.
- Decisions are never updated or deleted; `verify()` reports the first one that doesn't match
  (AT-49).
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import asdict, dataclass
from typing import Any

from amlcheck.core.audit import GENESIS, Verification, canonical_json, load, record_hash
from amlcheck.storage.db import transaction

KINDS = ("approved", "rejected", "escalated")
CLOSING = frozenset({"approved", "rejected"})


@dataclass(frozen=True)
class Decision:
    decision_id: str
    case_id: str
    check_id: str
    check_record_hash: str
    decision: str
    note: str
    operator: str
    created_at: str  # stored (`to_db`) form
    tool_version: str

    def body(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Stored:
    seq: int
    decision: Decision
    prev_hash: str
    record_hash: str


def decision_hash(prev_hash: str, d: Decision) -> str:
    return hashlib.sha256((prev_hash + canonical_json(d.body())).encode()).hexdigest()


def append(conn: sqlite3.Connection, d: Decision) -> Stored:
    """Append one decision; the head is read and extended in one write transaction."""
    with transaction(conn):
        row = conn.execute("SELECT record_hash FROM decisions ORDER BY seq DESC LIMIT 1").fetchone()
        prev = row[0] if row else GENESIS
        digest = decision_hash(prev, d)
        cur = conn.execute(
            "INSERT INTO decisions (decision_id, case_id, check_id, check_record_hash, decision, "
            "note, operator, created_at, tool_version, prev_hash, record_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                d.decision_id,
                d.case_id,
                d.check_id,
                d.check_record_hash,
                d.decision,
                d.note,
                d.operator,
                d.created_at,
                d.tool_version,
                prev,
                digest,
            ),
        )
    if cur.lastrowid is None:  # pragma: no cover - sqlite always gives one
        raise RuntimeError("decision not stored")
    return Stored(cur.lastrowid, d, prev, digest)


_COLS = (
    "seq, decision_id, case_id, check_id, check_record_hash, decision, note, operator, "
    "created_at, tool_version, prev_hash, record_hash"
)


def _row(r: tuple[Any, ...]) -> Stored:
    return Stored(r[0], Decision(*r[1:10]), r[10], r[11])


def stored(
    conn: sqlite3.Connection, *, case_id: str | None = None, check_id: str | None = None
) -> list[Stored]:
    rows = conn.execute(
        f"SELECT {_COLS} FROM decisions "  # noqa: S608 - constant columns
        "WHERE (?1 IS NULL OR case_id = ?1) AND (?2 IS NULL OR check_id = ?2) ORDER BY seq",
        (case_id, check_id),
    ).fetchall()
    return [_row(r) for r in rows]


def _check_hash(conn: sqlite3.Connection, check_id: str) -> str | None:
    """The check record's hash recomputed from its stored contents (None when it is gone)."""
    row = conn.execute("SELECT seq FROM checks WHERE check_id = ?", (check_id,)).fetchone()
    loaded = load(conn, int(row[0])) if row else None
    if loaded is None:
        return None
    record, prev, _ = loaded
    return record_hash(prev, record)


def verify(conn: sqlite3.Connection) -> Verification:
    """Recompute the decision chain and each decision's link to its check record."""
    prev = GENESIS
    count = 0
    expected_seq: int | None = None
    for r in conn.execute(f"SELECT {_COLS} FROM decisions ORDER BY seq").fetchall():  # noqa: S608
        s = _row(r)
        if expected_seq is not None and s.seq != expected_seq:
            return Verification(False, count, prev, s.seq, f"decisions missing before #{s.seq}")
        if s.prev_hash != prev:
            return Verification(False, count, prev, s.seq, "prev_hash does not match the chain")
        if decision_hash(prev, s.decision) != s.record_hash:
            return Verification(False, count, prev, s.seq, "contents do not match record_hash")
        if _check_hash(conn, s.decision.check_id) != s.decision.check_record_hash:
            return Verification(
                False, count, prev, s.seq, "the check record it was made on has changed or is gone"
            )
        prev = s.record_hash
        count += 1
        expected_seq = s.seq + 1
    return Verification(True, count, prev)
