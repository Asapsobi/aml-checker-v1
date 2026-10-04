"""Hash-chained, append-only audit log of checks (PRD F6, D-018).

`record_hash = sha256(prev_hash + canonical_json(record))`, where the record is the check row, its
sources and its findings. Nullable fields enter the record only when set, so a column added in a
later phase never changes the hash of a record written before it (F6.4). There is no update or
delete path.

Canonical JSON: sorted keys, no spaces, UTF-8, no floats (amounts and shares are decimal strings),
so the same record always hashes the same on every machine.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from amlcheck.storage.db import transaction

GENESIS = "0" * 64

#: Columns of `checks` that are hashed only when not NULL (F6.4). New nullable columns go here.
_OPTIONAL = ("amount", "client", "operator_note", "score_json", "trace_id", "label_json")


def canonical_json(obj: Any) -> str:
    if any(isinstance(v, float) for v in _walk(obj)):
        raise TypeError("floats are not allowed in an audit record; use Decimal or str")
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_default
    )


def _default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    raise TypeError(f"not allowed in an audit record: {type(value).__name__}")


def _normalise(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """What the database will give back, so hashing at write and at verify see the same thing."""
    result: dict[str, Any] = json.loads(canonical_json(dict(evidence)))
    return result


def _walk(value: Any) -> Iterator[Any]:
    yield value
    if isinstance(value, Mapping):
        for v in value.values():
            yield from _walk(v)
    elif isinstance(value, list | tuple):
        for v in value:
            yield from _walk(v)


@dataclass(frozen=True)
class AuditSource:
    source: str
    required: bool
    status: str
    summary: str
    evidence: Mapping[str, Any] = field(default_factory=dict)
    as_of: str | None = None
    attribution: str | None = None

    def body(self) -> dict[str, Any]:
        b: dict[str, Any] = {
            "source": self.source,
            "required": self.required,
            "status": self.status,
            "summary": self.summary,
            "evidence": _normalise(self.evidence),
        }
        if self.as_of is not None:
            b["as_of"] = self.as_of
        if self.attribution is not None:
            b["attribution"] = self.attribution
        return b


@dataclass(frozen=True)
class AuditFinding:
    rule_id: str
    severity: str
    source: str
    summary: str
    observed_at: str
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def body(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "source": self.source,
            "summary": self.summary,
            "observed_at": self.observed_at,
            "evidence": _normalise(self.evidence),
        }


@dataclass(frozen=True)
class AuditRecord:
    check_id: str
    created_at: str  # ISO-8601 UTC, as stored
    chain: str
    address_norm: str
    verdict: str
    tool_version: str
    rules_version: int
    config_hash: str
    sources: Sequence[AuditSource] = ()
    findings: Sequence[AuditFinding] = ()
    amount: str | None = None
    client: str | None = None
    operator_note: str | None = None
    score_json: str | None = None
    trace_id: str | None = None
    label_json: str | None = None  # P12: who the address is (methodology §11.6)

    def body(self) -> dict[str, Any]:
        check: dict[str, Any] = {
            "check_id": self.check_id,
            "created_at": self.created_at,
            "chain": self.chain,
            "address_norm": self.address_norm,
            "verdict": self.verdict,
            "tool_version": self.tool_version,
            "rules_version": self.rules_version,
            "config_hash": self.config_hash,
        }
        for name in _OPTIONAL:
            value = getattr(self, name)
            if value is not None:
                check[name] = value
        sources = sorted((s.body() for s in self.sources), key=canonical_json)
        findings = sorted((f.body() for f in self.findings), key=canonical_json)
        return {"check": check, "sources": sources, "findings": findings}


def record_hash(prev_hash: str, record: AuditRecord) -> str:
    return hashlib.sha256((prev_hash + canonical_json(record.body())).encode()).hexdigest()


@dataclass(frozen=True)
class Appended:
    seq: int
    prev_hash: str
    record_hash: str


def append(conn: sqlite3.Connection, record: AuditRecord) -> Appended:
    """Append one record. The chain head is read and extended in one write transaction, so two
    processes can never fork the chain."""
    with transaction(conn):
        row = conn.execute("SELECT record_hash FROM checks ORDER BY seq DESC LIMIT 1").fetchone()
        prev = row[0] if row else GENESIS
        digest = record_hash(prev, record)
        cur = conn.execute(
            "INSERT INTO checks (check_id, created_at, chain, address_norm, verdict, amount, "
            "client, operator_note, score_json, trace_id, tool_version, rules_version, "
            "config_hash, prev_hash, record_hash, label_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.check_id,
                record.created_at,
                record.chain,
                record.address_norm,
                record.verdict,
                record.amount,
                record.client,
                record.operator_note,
                record.score_json,
                record.trace_id,
                record.tool_version,
                record.rules_version,
                record.config_hash,
                prev,
                digest,
                record.label_json,
            ),
        )
        conn.executemany(
            "INSERT INTO check_sources (check_id, source, required, status, as_of, summary, "
            "evidence_meta_json, attribution) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    record.check_id,
                    s.source,
                    int(s.required),
                    s.status,
                    s.as_of,
                    s.summary,
                    canonical_json(_normalise(s.evidence)),
                    s.attribution,
                )
                for s in record.sources
            ],
        )
        conn.executemany(
            "INSERT INTO check_findings (check_id, rule_id, severity, source, summary, "
            "evidence_json, observed_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    record.check_id,
                    f.rule_id,
                    f.severity,
                    f.source,
                    f.summary,
                    canonical_json(_normalise(f.evidence)),
                    f.observed_at,
                )
                for f in record.findings
            ],
        )
        seq = cur.lastrowid
    if seq is None:  # sqlite always sets it after an INSERT
        raise RuntimeError("audit append got no row id")
    return Appended(seq, prev, digest)


def load(conn: sqlite3.Connection, seq: int) -> tuple[AuditRecord, str, str] | None:
    """A stored record rebuilt from its rows, with its stored prev_hash and record_hash."""
    row = conn.execute(
        "SELECT check_id, created_at, chain, address_norm, verdict, amount, client, "
        "operator_note, score_json, trace_id, tool_version, rules_version, config_hash, "
        "prev_hash, record_hash, label_json FROM checks WHERE seq = ?",
        (seq,),
    ).fetchone()
    if row is None:
        return None
    check_id = row[0]
    sources = tuple(
        AuditSource(
            source=r[0],
            required=bool(r[1]),
            status=r[2],
            as_of=r[3],
            summary=r[4],
            evidence=json.loads(r[5]),
            attribution=r[6],
        )
        for r in conn.execute(
            "SELECT source, required, status, as_of, summary, evidence_meta_json, attribution "
            "FROM check_sources WHERE check_id = ?",
            (check_id,),
        )
    )
    findings = tuple(
        AuditFinding(
            rule_id=r[0],
            severity=r[1],
            source=r[2],
            summary=r[3],
            evidence=json.loads(r[4]),
            observed_at=r[5],
        )
        for r in conn.execute(
            "SELECT rule_id, severity, source, summary, evidence_json, observed_at "
            "FROM check_findings WHERE check_id = ?",
            (check_id,),
        )
    )
    record = AuditRecord(
        check_id=check_id,
        created_at=row[1],
        chain=row[2],
        address_norm=row[3],
        verdict=row[4],
        amount=row[5],
        client=row[6],
        operator_note=row[7],
        score_json=row[8],
        trace_id=row[9],
        tool_version=row[10],
        rules_version=row[11],
        config_hash=row[12],
        sources=sources,
        findings=findings,
        label_json=row[15],
    )
    return record, row[13], row[14]


@dataclass(frozen=True)
class Verification:
    ok: bool
    records: int
    head_hash: str
    break_at: int | None = None  # seq of the first record that doesn't verify
    reason: str | None = None


def verify(conn: sqlite3.Connection) -> Verification:
    """Recompute the whole chain; report the first break (F6.3)."""
    prev = GENESIS
    count = 0
    expected_seq = None
    for (seq,) in conn.execute("SELECT seq FROM checks ORDER BY seq").fetchall():
        if expected_seq is not None and seq != expected_seq:
            return Verification(False, count, prev, seq, f"records missing before #{seq}")
        loaded = load(conn, seq)
        if loaded is None:
            return Verification(False, count, prev, seq, f"record #{seq} vanished while verifying")
        record, stored_prev, stored_hash = loaded
        if stored_prev != prev:
            return Verification(False, count, prev, seq, "link to the previous record is broken")
        if record_hash(prev, record) != stored_hash:
            return Verification(False, count, prev, seq, "record content changed")
        prev = stored_hash
        count += 1
        expected_seq = seq + 1
    return Verification(True, count, prev)
