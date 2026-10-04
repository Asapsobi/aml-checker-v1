"""`case export --jsonl`: what operators decided, with the evidence (PRD F12.4, D-061).

One line per decision, in decision-chain order, built from stored records only: the case, the
decision, and from the check it was made on its verdict, rule IDs, score components, the
classifier's types and profile, and the trace's partition and coverage; plus the case's inference
feedback.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from amlcheck.cases import cases, decisions
from amlcheck.core.audit import load


def _check(conn: sqlite3.Connection, check_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT seq FROM checks WHERE check_id = ?", (check_id,)).fetchone()
    loaded = load(conn, int(row[0])) if row else None
    if loaded is None:
        return {"check_id": check_id, "missing": True}
    record = loaded[0]
    sources = {s.source: s for s in record.sources}
    classifier = sources.get("classifier")
    trace = sources.get("trace")
    score = json.loads(record.score_json) if record.score_json else None
    return {
        "check_id": record.check_id,
        "created_at": record.created_at,
        "verdict": record.verdict,
        "amount_usdt": record.amount,
        "rules": sorted({f.rule_id for f in record.findings}),
        "score": score,
        "classification": (
            {
                "types": classifier.evidence.get("types", []),
                "profile": classifier.evidence.get("profile"),
                "version": classifier.evidence.get("classifier_version"),
            }
            if classifier and classifier.evidence
            else None
        ),
        "trace": (
            {
                "trace_id": trace.evidence.get("trace_id"),
                "partition": trace.evidence.get("partition"),
                "coverage": trace.evidence.get("coverage"),
                "hazard": trace.evidence.get("hazard"),
                "complete": trace.evidence.get("complete"),
            }
            if trace and trace.evidence
            else None
        ),
        "sources": {s.source: s.status for s in record.sources},
    }


def lines(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    out = []
    for s in decisions.stored(conn):
        d = s.decision
        case = cases.get(conn, d.case_id)
        out.append(
            {
                "decision": {
                    "seq": s.seq,
                    "decision_id": d.decision_id,
                    "decision": d.decision,
                    "note": d.note,
                    "operator": d.operator,
                    "created_at": d.created_at,
                    "record_hash": s.record_hash,
                },
                "case": {
                    "case_id": d.case_id,
                    "chain": case.chain.value if case else None,
                    "address": case.address if case else None,
                    "client": case.client if case else None,
                    "status": case.status if case else None,
                },
                "check": _check(conn, d.check_id),
                "feedback": [
                    {
                        "type": f.type,
                        "verdict": f.verdict,
                        "classifier_version": f.classifier_version,
                        "label_id": f.label_id,
                    }
                    for f in cases.feedback(conn, d.case_id)
                ],
            }
        )
    return out
