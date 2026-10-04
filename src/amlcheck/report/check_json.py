"""A check as JSON, from its stored audit record (PRD F14.1).

`check --json`, `investigate --json`, the API's `POST /v1/check` (first answer and every
idempotent replay) and `GET /v1/checks/{id}` all use this one function, so the same check always
reads the same, byte for byte. Built only from what the audit log holds.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from amlcheck.cases import decisions
from amlcheck.core.audit import load
from amlcheck.core.clock import from_iso, to_iso
from amlcheck.core.models import Verdict
from amlcheck.core.score import from_json
from amlcheck.core.verdict import ACTION, DISCLAIMER
from amlcheck.report.case_report import LABELS


def check_json(conn: sqlite3.Connection, check_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT seq FROM checks WHERE check_id = ?", (check_id,)).fetchone()
    loaded = load(conn, int(row[0])) if row else None
    if loaded is None:
        return None
    r, _, record_hash = loaded
    score = from_json(r.score_json) if r.score_json else None
    return {
        "check_id": r.check_id,
        "verdict": r.verdict,
        "action": ACTION[Verdict(r.verdict)],
        "chain": r.chain,
        "address": r.address_norm,
        "checked_at": to_iso(from_iso(r.created_at)),
        "amount_usdt": r.amount,
        "client": r.client,
        "note": r.operator_note,
        "score": {**score.to_json(), "shown": score.shown} if score else None,
        "findings": [
            {
                "rule_id": f.rule_id,
                "severity": f.severity,
                "source": f.source,
                "summary": f.summary,
                "observed_at": f.observed_at,
                "evidence": f.evidence,
            }
            for f in sorted(r.findings, key=lambda f: (f.rule_id, f.source, f.summary))
        ],
        "sources": [
            {
                "source": s.source,
                "label": LABELS.get(s.source, s.source),
                "required": s.required,
                "status": s.status,
                "detail": s.summary,
                "observed_at": s.as_of,
                "evidence": s.evidence,
            }
            for s in sorted(r.sources, key=lambda s: s.source)
        ],
        "trace_id": r.trace_id,
        "decisions": [
            {**d.decision.body(), "seq": d.seq, "record_hash": d.record_hash}
            for d in decisions.stored(conn, check_id=r.check_id)
        ],
        "audit": {"record_hash": record_hash},
        "tool_version": r.tool_version,
        "config_hash": r.config_hash,
        "disclaimer": DISCLAIMER,
    }
