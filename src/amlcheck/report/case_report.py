"""Per-counterparty case report PDF (PRD F10.4, D-053).

- Built only from stored records: the check's audit record (sources, findings, score), the trace
  linked to it, the registry row, labels and entity. No network, so the report always shows what was
  recorded at the time of the check, and the same records give the same PDF bytes (`invariant`).
- The record's own hash is recomputed and shown; `amlcheck audit verify` checks the whole chain.
- Sections: verdict and score, findings, sources, USDT history, classification, the decisions made
  on this check (D-060), the record, then source of funds with the graph (the SVG's layout).
- Built-in PDF fonts (no embedding): text the WinAnsi encoding can't hold is shown as `?`; `≥` and
  arrows are spelled out. Marked internal use only on every page (D-023).
"""

from __future__ import annotations

import io
import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from importlib.metadata import version
from typing import Any
from xml.sax.saxutils import escape

from reportlab.graphics.shapes import Drawing, Group, Path, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    CondPageBreak,
    Flowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from amlcheck.cases import decisions as decisions_chain
from amlcheck.chain.base import usdt
from amlcheck.core import risk
from amlcheck.core import score as scoring
from amlcheck.core.audit import AuditRecord, load, record_hash
from amlcheck.core.models import Address, Verdict
from amlcheck.core.verdict import ACTION
from amlcheck.intel import registry
from amlcheck.intel.lookalike import LABEL as LOOKALIKE_LABEL
from amlcheck.intel.lookalike import SOURCE as LOOKALIKE
from amlcheck.intel.names import counterparty_text, label_text
from amlcheck.intel.store import IntelStore
from amlcheck.profile.adapter import LABEL as CLASSIFIER_LABEL
from amlcheck.profile.adapter import SOURCE as CLASSIFIER
from amlcheck.screening import lists
from amlcheck.screening.bsc_freeze import LABEL as BSC_FREEZE_LABEL
from amlcheck.screening.bsc_freeze import SOURCE as BSC_FREEZE
from amlcheck.screening.exposure import LABEL as EXPOSURE_LABEL
from amlcheck.screening.exposure import SOURCE as EXPOSURE
from amlcheck.screening.sanctions import LABEL as SANCTIONS_LABEL
from amlcheck.screening.sanctions import SOURCE as SANCTIONS
from amlcheck.screening.tron_freeze import INDEX_LABEL, INDEX_SOURCE, SPOT_LABEL, SPOT_SOURCE
from amlcheck.trace import graph
from amlcheck.trace.adapter import LABEL as TRACE_LABEL
from amlcheck.trace.adapter import SOURCE as TRACE
from amlcheck.trace.jobs import read_job
from amlcheck.trace.model import Trace, pct

LABELS = {
    SANCTIONS: SANCTIONS_LABEL,
    INDEX_SOURCE: INDEX_LABEL,
    SPOT_SOURCE: SPOT_LABEL,
    BSC_FREEZE: BSC_FREEZE_LABEL,
    EXPOSURE: EXPOSURE_LABEL,
    LOOKALIKE: LOOKALIKE_LABEL,
    CLASSIFIER: CLASSIFIER_LABEL,
    TRACE: TRACE_LABEL,
    **{spec.source: spec.label for spec in (lists.UK, lists.EU, lists.NBCTF)},
    "engine": "amlcheck",
}
_SPELL = {"≥": ">=", "≤": "<=", "←": "<-", "→": "->", "✓": "ok"}
PAGE_W, PAGE_H = A4
MARGIN = 18 * mm
TEXT_W = PAGE_W - 2 * MARGIN
GRAPH_H = PAGE_H - 2 * MARGIN - 40 * mm  # leaves room for the heading on the graph's page


class ReportError(ValueError):
    """The report can't be built; the message is meant for the operator."""


@dataclass(frozen=True)
class CaseData:
    address: Address
    seq: int
    record: AuditRecord
    record_hash: str
    hash_ok: bool  # this record's stored hash matches its contents
    counterparty: registry.Counterparty | None
    labels: tuple[str, ...]
    entity: str | None
    category: str | None
    trace: Trace | None
    trace_status: str | None
    decisions: tuple[decisions_chain.Stored, ...] = ()
    case_status: str | None = None
    trace_out: Trace | None = None  # P13: where the money went (methodology §12.2)
    trace_out_id: str | None = None


def gather(conn: sqlite3.Connection, address: Address, check_id: str | None = None) -> CaseData:
    """Everything the report shows, from the database only (D-053)."""
    if check_id:
        row = conn.execute(
            "SELECT seq, chain, address_norm FROM checks WHERE check_id = ?", (check_id,)
        ).fetchone()
        if row is None:
            raise ReportError(f"no check {check_id}")
        if (row[1], row[2]) != (address.chain.value, address.norm):
            raise ReportError(f"check {check_id} is for {row[2]}, not {address.norm}")
    else:
        row = conn.execute(
            "SELECT seq FROM checks WHERE chain = ? AND address_norm = ? ORDER BY seq DESC LIMIT 1",
            (address.chain.value, address.norm),
        ).fetchone()
        if row is None:
            raise ReportError(f"{address.norm} has never been checked; run `amlcheck check` first")
    loaded = load(conn, int(row[0]))
    if loaded is None:  # pragma: no cover - the row was just read
        raise ReportError("the check record could not be read")
    record, prev, stored = loaded
    store = IntelStore(conn)
    entity = store.entity_for(address.chain, address.norm)
    terminal = store.best_terminal(address.chain, address.norm)
    trace = status = None
    if record.trace_id:
        found = read_job(conn, record.trace_id)
        if found is not None:
            trace, status = found.result or found.partial, found.status
    trace_ev = next((s.evidence for s in record.sources if s.source == TRACE), {})
    out_id = (trace_ev.get("out") or {}).get("trace_id")
    out_job = read_job(conn, str(out_id)) if out_id else None
    return CaseData(
        address=address,
        seq=int(row[0]),
        record=record,
        record_hash=stored,
        hash_ok=record_hash(prev, record) == stored,
        counterparty=registry.get(conn, address.chain, address.norm),
        labels=tuple(
            f"{x.category} ({x.provenance})" for x in store.labels(address.chain, address.norm)
        ),
        entity=(
            f"#{entity.id} {entity.name} ({entity.kind}), role {entity.role}" if entity else None
        ),
        category=terminal.category if terminal else None,
        trace=trace,
        trace_status=status,
        trace_out=(out_job.result or out_job.partial) if out_job else None,
        trace_out_id=str(out_id) if out_id else None,
        decisions=tuple(decisions_chain.stored(conn, check_id=record.check_id)),
        case_status=(
            "open"
            if conn.execute(
                "SELECT 1 FROM cases WHERE opened_from = ? AND status = 'open'",
                (record.check_id,),
            ).fetchone()
            else None
        ),
    )


# --- text -----------------------------------------------------------------------------------------


def text(value: object) -> str:
    """Plain text the built-in fonts can draw (WinAnsi), escaped for Paragraph markup."""
    s = str(value)
    for a, b in _SPELL.items():
        s = s.replace(a, b)
    return escape(s.encode("cp1252", errors="replace").decode("cp1252"))


def raw(value: object) -> str:
    """Like `text`, for drawing strings directly (no markup)."""
    s = str(value)
    for a, b in _SPELL.items():
        s = s.replace(a, b)
    return s.encode("cp1252", errors="replace").decode("cp1252")


_SS = getSampleStyleSheet()
H1 = ParagraphStyle("h1", parent=_SS["Heading1"], fontSize=16, spaceAfter=4)
H2 = ParagraphStyle(
    "h2", parent=_SS["Heading2"], fontSize=12, spaceBefore=10, spaceAfter=4, keepWithNext=1
)
BODY = ParagraphStyle("body", parent=_SS["BodyText"], fontSize=9, leading=12)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=8, leading=10)
MONO = ParagraphStyle("mono", parent=BODY, fontName="Courier", fontSize=8.5, leading=11)
CELL = ParagraphStyle("cell", parent=BODY, fontSize=8, leading=10)
CELL_MONO = ParagraphStyle("cellmono", parent=CELL, fontName="Courier", fontSize=7.5)
VERDICT_COLOUR = {
    Verdict.BLOCK: "#b42318",
    Verdict.INCOMPLETE: "#b45309",
    Verdict.REVIEW: "#c2410c",
    Verdict.NO_HITS: "#15803d",
}


def _p(s: str, style: ParagraphStyle = BODY) -> Paragraph:
    return Paragraph(s, style)


def _table(rows: list[list[Any]], widths: Sequence[float], head: bool = True) -> Table:
    t = Table(rows, colWidths=list(widths), repeatRows=1 if head else 0, hAlign="LEFT")
    style: list[Any] = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#9ca3af")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f9fafb")]),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]
    if head:
        style.append(("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"))
    t.setStyle(TableStyle(style))
    return t


# --- sections -------------------------------------------------------------------------------------


def _header(d: CaseData) -> list[Flowable]:
    r = d.record
    verdict = Verdict(r.verdict)
    out: list[Flowable] = [
        _p("Case report", H1),
        _p(f"{text(d.address.chain.value.upper())} counterparty", SMALL),
        _p(text(d.address.norm), ParagraphStyle("addr", parent=MONO, fontSize=11, leading=14)),
        Spacer(1, 4),
    ]
    facts = [
        f"Check <b>{text(r.check_id)}</b> · recorded {text(r.created_at[:19])}Z · "
        f"amlcheck {text(r.tool_version)} · rules v{r.rules_version}",
    ]
    if r.amount or r.client:
        bits = []
        if r.amount:
            bits.append(f"amount {text(r.amount)} USDT")
        if r.client:
            bits.append(f"client {text(r.client)}")
        facts.append(" · ".join(bits))
    if r.operator_note:
        facts.append(f"note: {text(r.operator_note)}")
    if d.counterparty:
        c = d.counterparty
        facts.append(
            f"Checked {c.check_count} time(s), first {text(c.first_screened_at[:10])}, last "
            f"{text(c.last_screened_at[:10])}; clients {text(', '.join(c.clients) or '-')}"
        )
    known = []
    if d.category:
        known.append(f"category {text(d.category)}")
    if d.labels:
        known.append(f"labels {text(', '.join(d.labels))}")
    if d.entity:
        known.append(f"entity {text(d.entity)}")
    if known:
        facts.append("Known: " + " · ".join(known))
    out += [_p(f, SMALL) for f in facts]
    colour = VERDICT_COLOUR[verdict]
    out += [
        Spacer(1, 8),
        _p(
            f'<font size="15" color="{colour}"><b>{verdict.value}</b></font>'
            f"&nbsp;&nbsp;{text(ACTION[verdict])}",
            ParagraphStyle("verdict", parent=BODY, fontSize=11, leading=18),
        ),
    ]
    who = label_text(json.loads(r.label_json) if r.label_json else None)
    if who:
        out.append(_p(f"Who: <b>{text(who)}</b>", BODY))
    if r.score_json:
        s = scoring.from_json(r.score_json)
        bound = " (lower bound: a required source is missing)" if s.lower_bound else ""
        hazard = ""
        if isinstance(s, scoring.Score):
            hazard = f" · H in {pct(s.hazard_in)}, out {pct(s.hazard_out)}"
        out.append(
            _p(
                f"<b>Score {text(s.shown)}</b>{text(bound)} · {text(s.breakdown)}{hazard} "
                f"· score v{s.version}",
                BODY,
            )
        )
    else:
        out.append(_p("No score: this check was recorded before scores existed (P7).", SMALL))
    out += [_p(text(line), SMALL) for line in risk.detail_list(_exposures(d))]
    return out


def _exposures(d: CaseData) -> list[risk.Exposure]:
    return risk.from_evidence(s.evidence for s in d.record.sources)


def _risk(d: CaseData) -> list[Flowable]:
    """The exposures behind the score (methodology §11.1), heaviest first."""
    found = _exposures(d)
    if not found:
        return []
    s = scoring.from_json(d.record.score_json) if d.record.score_json else None
    decay = scoring.decay_of(s)
    rows: list[list[Any]] = [["Dir", "Hop", "Risk type", "Share", "USDT", "Entity · address"]]
    for e in risk.ranked(found, decay)[:30]:
        inferred = f" (inferred, {e.confidence})" if e.confidence is not None else ""
        rows.append(
            [
                e.direction,
                e.hop,
                _p(text(e.risk_type), CELL),
                pct(e.percent),
                usdt(e.volume),
                _p(text(f"{e.entity}{inferred} · {e.address}"), CELL_MONO),
            ]
        )
    return [
        _p("Exposures", H2),
        _p(
            "Direct amounts are exact; an indirect amount is its path volume, the smallest amount "
            "on the path, which every hop moved (D-078).",
            SMALL,
        ),
        _table(rows, [10 * mm, 10 * mm, 28 * mm, 16 * mm, 24 * mm, TEXT_W - 88 * mm]),
    ]


def _findings(d: CaseData) -> list[Flowable]:
    out: list[Flowable] = [_p("Findings", H2)]
    if not d.record.findings:
        return [*out, _p("None: nothing was found in the sources checked, as of the times shown.")]
    rows: list[list[Any]] = [["Rule", "Severity", "What was found"]]
    for f in sorted(d.record.findings, key=lambda f: (f.rule_id, f.summary)):
        low = " (low priority)" if f.evidence.get("priority") == "low" else ""
        rows.append([f.rule_id, f.severity, _p(text(f.summary + low), CELL)])
    out.append(_table(rows, [22 * mm, 22 * mm, TEXT_W - 44 * mm]))
    return out


def _sources(d: CaseData) -> list[Flowable]:
    rows: list[list[Any]] = [["Source", "Status", "Detail", "As of"]]
    for s in sorted(d.record.sources, key=lambda s: s.source):
        need = "" if s.required else " (not required)"
        rows.append(
            [
                _p(text(LABELS.get(s.source, s.source) + need), CELL),
                s.status,
                _p(text(s.summary), CELL),
                _p(text((s.as_of or "-")[:19]), CELL),
            ]
        )
    return [_p("Sources", H2), _table(rows, [44 * mm, 16 * mm, TEXT_W - 94 * mm, 34 * mm])]


def _exposure(d: CaseData) -> list[Flowable]:
    ev = next((s.evidence for s in d.record.sources if s.source == EXPOSURE and s.evidence), None)
    if not ev:
        return []
    out: list[Flowable] = [
        _p("USDT history", H2),
        _p(
            text(
                f"{ev['transfers']} transfer(s) since {str(ev['since'])[:10]} · received "
                f"{usdt(ev['received_usdt'])} · sent {usdt(ev['sent_usdt'])} USDT · first activity "
                f"{ev.get('first_activity') or 'none found'}"
            )
        ),
    ]
    if ev.get("zero_value_dropped"):
        out.append(
            _p(text(f"{ev['zero_value_dropped']} 0-value transfer(s) dropped (address poisoning)"))
        )
    shown = ev.get("counterparties") or []
    if shown:
        rows: list[list[Any]] = [["Counterparty", "Received", "Sent", "Txs", "Known as"]]
        for c in shown:
            rows.append(
                [
                    _p(text(c["address"]), CELL_MONO),
                    usdt(c["received_usdt"]),
                    usdt(c["sent_usdt"]),
                    c["transfers"],
                    _p(text(counterparty_text(c)), CELL),
                ]
            )
        out += [
            Spacer(1, 3),
            _p(f"Largest {len(shown)} of {ev['counterparty_count']} counterparties", SMALL),
            _table(rows, [74 * mm, 24 * mm, 24 * mm, 12 * mm, TEXT_W - 134 * mm]),
        ]
    return out


def _trace(d: CaseData) -> list[Flowable]:
    t = d.trace
    if t is None:
        if d.record.trace_id:
            return [_p("Source of funds", H2), _p(text(f"Trace {d.record.trace_id} not found."))]
        return [
            _p("Source of funds", H2),
            _p("No trace ran with this check (run `amlcheck investigate` for one).", SMALL),
        ]
    out = _trace_body(t, "Source of funds", "received", d.record.trace_id)
    out += [CondPageBreak(GRAPH_H / 2), _p("Trace graph", H2), _graph(t)]
    if d.trace_out is not None:
        out += _trace_body(d.trace_out, "Destination of funds", "sent", d.trace_out_id)
    return out


def _trace_body(t: Trace, title: str, flow: str, trace_id: str | None) -> list[Flowable]:
    """One direction's breakdown and top paths."""
    coverage = pct(t.coverage) if t.coverage is not None else f"nothing {flow} in the window"
    state = "" if t.complete else f" · INCOMPLETE: {t.failure or 'stopped early'}"
    if t.stopped == "time":
        state += " · stopped at the time budget (the rest is untraced:budget)"
    out: list[Flowable] = [
        _p(title, H2),
        _p(
            text(
                f"{usdt(t.target_inflow)} USDT {flow} in the window · coverage {coverage} · "
                f"{t.budget.nodes_read} address(es) read · trace {trace_id}{state}"
            )
        ),
        _p("Shares are proportional estimates: USDT is fungible, so no amount is exact.", SMALL),
    ]
    rows: list[list[Any]] = [["Category", "Share", "~ USDT"]]
    for category, share in sorted(t.partition.items(), key=lambda kv: (-kv[1], kv[0])):
        rows.append([category, pct(share), usdt(share * t.target_inflow)])
    layering = t.annotations.get("layering")
    if layering:
        rows.append(["layering (annotation)", pct(layering), ""])
    out.append(_table(rows, [60 * mm, 22 * mm, 30 * mm]))
    if t.paths:
        arrow = " <- " if t.direction == "in" else " -> "
        prows: list[list[Any]] = [["To", "Bottleneck", "Estimate", "Path (target first)"]]
        for p in t.paths[:8]:
            prows.append(
                [
                    _p(text(p.to_category), CELL),
                    usdt(p.bottleneck),
                    usdt(p.estimated),
                    _p(text(arrow.join(graph.short(a) for a in p.addresses)), CELL_MONO),
                ]
            )
        out += [
            Spacer(1, 4),
            _p("Bottleneck: the smallest hop on the path; every hop moved at least that.", SMALL),
            _table(prows, [38 * mm, 20 * mm, 20 * mm, TEXT_W - 78 * mm]),
        ]
    return out


def _graph(t: Trace) -> Drawing:
    """The SVG's picture (same layout), drawn natively so the PDF needs no converter."""
    lay = graph.layout(t)
    top = graph.TOP - 30  # the SVG's title rows are the report's headings here
    w, h = lay.width, lay.height - top
    s = min(1.0, TEXT_W / w, GRAPH_H / h)

    def y(v: float) -> float:
        return h - (v - top)

    g = Group()
    for hop in range(lay.hops + 1):
        g.add(
            String(
                graph.column_x(t, lay.hops, hop),
                y(graph.TOP - 12),
                "TARGET" if hop == 0 else f"HOP {hop}",
                fontName="Helvetica",
                fontSize=11,
                fillColor=colors.HexColor("#6b7280"),
            )
        )
    for e in lay.links:
        mid = (e.x1 + e.x2) / 2
        c = colors.HexColor(e.colour)
        p = Path(
            strokeColor=colors.Color(c.red, c.green, c.blue, alpha=0.45),
            strokeWidth=round(e.width, 1),
            fillColor=None,
        )
        p.moveTo(e.x1, y(e.y1))
        p.curveTo(mid, y(e.y1), mid, y(e.y2), e.x2, y(e.y2))
        g.add(p)
    for b in lay.boxes:
        fill, stroke, _ = graph.GROUPS[b.group]
        box = Rect(b.x, y(b.y + graph.BOX_H), graph.BOX_W, graph.BOX_H, rx=6, ry=6)
        box.fillColor = colors.HexColor(fill)
        box.strokeColor = colors.HexColor(stroke)
        box.strokeWidth = 1.5
        if b.group == "untraced":
            box.strokeDashArray = [4, 3]
        mark = Rect(b.x, y(b.y + graph.BOX_H), 6, graph.BOX_H)
        mark.fillColor = colors.HexColor(stroke)
        mark.strokeColor = None
        g.add(box)
        g.add(mark)
        dark = colors.HexColor("#111827")
        g.add(
            String(
                b.x + 14,
                y(b.y + 19),
                raw(graph.short(b.node.address)),
                fontName="Courier",
                fontSize=12.5,
                fillColor=dark,
            )
        )
        g.add(
            String(
                b.x + graph.BOX_W - 8,
                y(b.y + 19),
                pct(b.node.weight),
                fontName="Helvetica-Bold",
                fontSize=11.5,
                fillColor=dark,
                textAnchor="end",
            )
        )
        g.add(
            String(
                b.x + 14,
                y(b.y + 36),
                raw(b.what),
                fontName="Helvetica",
                fontSize=11,
                fillColor=colors.HexColor("#374151"),
            )
        )
    g.transform = (s, 0, 0, s, 0, 0)
    drawing = Drawing(w * s, h * s)
    drawing.add(g)
    return drawing


def _classification(d: CaseData) -> list[Flowable]:
    src = next((s for s in d.record.sources if s.source == CLASSIFIER), None)
    out: list[Flowable] = [_p("Classification", H2)]
    if src is None:
        return [*out, _p("The classifier did not run with this check.", SMALL)]
    types = src.evidence.get("types") or []
    if not types:
        return [*out, _p(text(src.summary))]
    rows: list[list[Any]] = [["Type", "Confidence", ""]]
    for x in types:
        rows.append([x["type"], x["confidence"], "primary" if x.get("primary") else ""])
    return [
        *out,
        _table(rows, [40 * mm, 24 * mm, 24 * mm]),
        _p("Types are inferences from the address's own transfers, never a verdict.", SMALL),
    ]


def _decision(d: CaseData) -> list[Flowable]:
    out: list[Flowable] = [_p("Decision", H2)]
    if not d.decisions:
        open_case = f" A case is open ({d.case_status})." if d.case_status else ""
        return [*out, _p(f"No decision recorded on this check.{text(open_case)}")]
    rows: list[list[Any]] = [["When (UTC)", "Decision", "By", "Note"]]
    for s in d.decisions:
        rows.append(
            [
                s.decision.created_at[:16].replace("T", " "),
                s.decision.decision.upper(),
                _p(text(s.decision.operator), CELL),
                _p(text(s.decision.note), CELL),
            ]
        )
    return [
        *out,
        _table(rows, [28 * mm, 22 * mm, 26 * mm, TEXT_W - 76 * mm]),
        _p(
            text(
                f"Decision chain #{d.decisions[-1].seq}, hash {d.decisions[-1].record_hash}; "
                "each decision is tied to this check's record hash."
            ),
            SMALL,
        ),
    ]


def _integrity(d: CaseData) -> list[Flowable]:
    ok = "matches its contents" if d.hash_ok else "DOES NOT MATCH its contents"
    return [
        _p("Record", H2),
        _p(
            text(
                f"Audit record #{d.seq}, hash {d.record_hash}: {ok}. Verify the whole chain with "
                "`amlcheck audit verify`."
            ),
            SMALL,
        ),
        _p(
            "Internal use only. NO_HITS means nothing was found in the sources checked, as of the "
            "times shown; it is not a clearance. Built from stored records only.",
            SMALL,
        ),
    ]


def render(d: CaseData) -> bytes:
    """The PDF. Same records and same amlcheck version ⇒ the same bytes."""
    buf = io.BytesIO()
    footer = raw(
        f"amlcheck {version('amlcheck')} · case report · {d.address.chain.value.upper()} "
        f"{graph.short(d.address.norm)} · check {d.record.check_id[:8]} · INTERNAL USE ONLY"
    )

    def on_page(canvas: Canvas, doc: SimpleDocTemplate) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#6b7280"))
        canvas.drawString(MARGIN, 10 * mm, footer)
        canvas.drawRightString(PAGE_W - MARGIN, 10 * mm, f"page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
        title=raw(f"Case report {d.address.norm}"),
        author="amlcheck",
        subject=raw(f"check {d.record.check_id}"),
        creator="amlcheck",
        invariant=1,
    )
    story = (
        _header(d)
        + _risk(d)
        + _findings(d)
        + _sources(d)
        + _exposure(d)
        + _classification(d)
        + _decision(d)
        + _integrity(d)
        + _trace(d)  # last: the graph may take a page of its own
    )
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    return buf.getvalue()


def summary(d: CaseData) -> dict[str, Any]:
    """What the CLI prints after writing the file."""
    s = scoring.from_json(d.record.score_json) if d.record.score_json else None
    return {
        "check_id": d.record.check_id,
        "verdict": d.record.verdict,
        "score": s.shown if s else None,
        "trace_id": d.record.trace_id,
        "hash_ok": d.hash_ok,
        "findings": sorted({f.rule_id for f in d.record.findings}),
    }
