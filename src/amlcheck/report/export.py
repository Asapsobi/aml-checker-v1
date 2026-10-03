"""Audit export CSV/JSON/PDF, CSV-injection safe (PRD F11, AT-47).

- CSV: one row per check. A spreadsheet runs a cell that starts with `=`, `+`, `-`, `@`, a tab or a
  carriage return as a formula; addresses, notes and client names come from outside, so every cell
  goes through `csv_cell`, which puts a `'` in front of such a value (OWASP CSV injection).
- JSON: every record exactly as hashed, with its `prev_hash` and `record_hash`, so the chain can be
  re-verified from the export alone: sha256 of `prev_hash` + the record as canonical JSON (keys
  sorted, no spaces, UTF-8).
- PDF: a table of the checks for people; built-in fonts and invariant metadata, as the case report.
- Exports are read-only and from stored records only.
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from importlib.metadata import version
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from amlcheck.core.audit import canonical_json, load
from amlcheck.core.score import from_json, shown
from amlcheck.report.case_report import text as _pdf_text

_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
CSV_COLUMNS = (
    "seq",
    "check_id",
    "created_at",
    "chain",
    "address",
    "verdict",
    "score",
    "band",
    "amount_usdt",
    "client",
    "note",
    "rules",
    "trace_id",
    "record_hash",
)


def csv_cell(value: object) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA_START) else text


@dataclass(frozen=True)
class Filter:
    chain: str | None = None
    address: str | None = None
    verdict: str | None = None
    client: str | None = None
    since: str | None = None  # stored (`to_db`) form, compared as text
    until: str | None = None

    def describe(self) -> str:
        parts = [f"{k} {v}" for k, v in vars(self).items() if v is not None and k != "chain"]
        return ", ".join(parts) or "all records"


@dataclass(frozen=True)
class Row:
    seq: int
    check_id: str
    created_at: str
    chain: str
    address: str
    verdict: str
    score_json: str | None
    amount: str | None
    client: str | None
    note: str | None
    trace_id: str | None
    record_hash: str
    rules: tuple[str, ...]

    @property
    def score(self) -> tuple[int | None, str]:
        if not self.score_json:
            return None, ""
        s = from_json(self.score_json)
        return s.score, s.band + ("+" if s.lower_bound else "")


def rows(conn: sqlite3.Connection, f: Filter) -> list[Row]:
    """Matching checks, oldest first (the chain's order)."""
    found = conn.execute(
        "SELECT seq, check_id, created_at, chain, address_norm, verdict, score_json, amount, "
        "client, operator_note, trace_id, record_hash FROM checks "
        "WHERE (?1 IS NULL OR (chain = ?1 AND address_norm = ?2)) "
        "AND (?3 IS NULL OR verdict = ?3) "
        "AND (?4 IS NULL OR client = ?4 COLLATE NOCASE) "
        "AND (?5 IS NULL OR created_at >= ?5) AND (?6 IS NULL OR created_at < ?6) "
        "ORDER BY seq",
        (f.chain, f.address, f.verdict, f.client, f.since, f.until),
    ).fetchall()
    rules: dict[str, set[str]] = {}
    for check_id, rule_id in conn.execute(
        "SELECT check_id, rule_id FROM check_findings WHERE check_id IN "
        "(SELECT check_id FROM checks WHERE (?1 IS NULL OR (chain = ?1 AND address_norm = ?2)))",
        (f.chain, f.address),
    ):
        rules.setdefault(check_id, set()).add(rule_id)
    return [
        Row(
            seq=r[0],
            check_id=r[1],
            created_at=r[2],
            chain=r[3],
            address=r[4],
            verdict=r[5],
            score_json=r[6],
            amount=r[7],
            client=r[8],
            note=r[9],
            trace_id=r[10],
            record_hash=r[11],
            rules=tuple(sorted(rules.get(r[1], ()))),
        )
        for r in found
    ]


def to_csv(found: Sequence[Row]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(CSV_COLUMNS)
    for r in found:
        score, band = r.score
        w.writerow(
            csv_cell(v)
            for v in (
                r.seq,
                r.check_id,
                r.created_at,
                r.chain,
                r.address,
                r.verdict,
                score,
                band,
                r.amount,
                r.client,
                r.note,
                " ".join(r.rules),
                r.trace_id,
                r.record_hash,
            )
        )
    return buf.getvalue()


def to_json(conn: sqlite3.Connection, found: Sequence[Row]) -> str:
    out: list[dict[str, Any]] = []
    for r in found:
        loaded = load(conn, r.seq)
        if loaded is None:  # pragma: no cover - the row was just read
            continue
        record, prev, digest = loaded
        out.append(
            {"seq": r.seq, "prev_hash": prev, "record_hash": digest, "record": record.body()}
        )
    # Canonical form inside each record: the hash can be recomputed from the export as is.
    return json.dumps(json.loads(canonical_json(out)), indent=2, ensure_ascii=False) + "\n"


_SS = getSampleStyleSheet()
_CELL = ParagraphStyle("cell", parent=_SS["BodyText"], fontSize=7, leading=8.5)
_MONO = ParagraphStyle("mono", parent=_CELL, fontName="Courier", fontSize=6.5)


def to_pdf(found: Sequence[Row], what: Filter) -> bytes:
    buf = io.BytesIO()
    page = landscape(A4)
    doc = SimpleDocTemplate(
        buf,
        pagesize=page,
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=14 * mm,
        title="amlcheck audit export",
        author="amlcheck",
        creator="amlcheck",
        invariant=1,
    )
    head = ["#", "Recorded (UTC)", "Verdict", "Score", "Chain", "Address", "Client", "Rules"]
    table: list[list[Any]] = [head]
    for r in found:
        score, _ = r.score
        table.append(
            [
                r.seq,
                r.created_at[:19],
                r.verdict,
                shown(score, r.verdict) if score is not None else "-",
                r.chain,
                Paragraph(_pdf_text(r.address), _MONO),
                Paragraph(_pdf_text(r.client or ""), _CELL),
                Paragraph(_pdf_text(" ".join(r.rules)), _CELL),
            ]
        )
    widths = [12 * mm, 34 * mm, 22 * mm, 28 * mm, 12 * mm, 78 * mm, 30 * mm]
    widths.append(page[0] - 24 * mm - sum(widths))
    t = Table(table, colWidths=widths, repeatRows=1, hAlign="LEFT")
    style: list[Any] = [
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#9ca3af")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f9fafb")]),
    ]
    t.setStyle(TableStyle(style))
    footer = f"amlcheck {version('amlcheck')} · audit export · INTERNAL USE ONLY"

    def on_page(canvas: Canvas, d: SimpleDocTemplate) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#6b7280"))
        canvas.drawString(12 * mm, 8 * mm, footer)
        canvas.drawRightString(page[0] - 12 * mm, 8 * mm, f"page {d.page}")
        canvas.restoreState()

    intro = (
        f"{len(found)} check(s) · {_pdf_text(what.describe())} · from the audit log, oldest first. "
        "Verify the chain with <b>amlcheck audit verify</b>; the JSON export carries every hash."
    )
    doc.build(
        [
            Paragraph("Audit export", _SS["Heading1"]),
            Paragraph(intro, _SS["BodyText"]),
            Spacer(1, 6),
            t,
        ],
        onFirstPage=on_page,
        onLaterPages=on_page,
    )
    return buf.getvalue()
