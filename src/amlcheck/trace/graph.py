"""Layered SVG trace graph (PRD F9.6).

- One column per hop. Money flows left to right: for a backward trace the target is on the right and
  its senders' senders on the left; a forward trace is the mirror image.
- Every trace item is a box: an address reached by two paths appears twice, as in the trace itself.
- Boxes show at least the first 8 and last 6 characters, so look-alikes stay distinguishable
  (methodology §4); the full address is in the box's tooltip.
- A coloured mark per category group, and a legend with each bucket's share of the traced value.
- Edge width grows with the amount sent along it.
- Plain text output with fixed rounding and sorted order: same trace ⇒ same bytes (snapshot test).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from xml.sax.saxutils import escape

from amlcheck.core.clock import to_iso
from amlcheck.intel.categories import CATEGORIES
from amlcheck.trace.model import Node, Trace, dec, pct

COL_W = 270
BOX_W = 220
BOX_H = 46
ROW_GAP = 14
MARGIN = 24
TOP = 96  # title, subtitle and column headers
LEGEND_ROW = 20
MAX_EDGE_W = 12.0

#: (fill, stroke) per group. Muted fills keep the text readable in print (case report, P7).
GROUPS: dict[str, tuple[str, str, str]] = {
    "target": ("#eef2ff", "#3730a3", "target"),
    "expanded": ("#ffffff", "#6b7280", "followed further"),
    "high": ("#fde2e2", "#b42318", "high risk"),
    "inferred": ("#fef3c7", "#b45309", "inferred, suspicious"),
    "elevated": ("#ffedd5", "#c2410c", "elevated risk"),
    "low": ("#e0f2fe", "#0369a1", "service, low risk"),
    "clean": ("#dcfce7", "#15803d", "regulated or trusted"),
    "untraced": ("#f3f4f6", "#9ca3af", "untraced"),
}
_HIGH = frozenset(c.name for c in CATEGORIES if c.high_risk)
_INFERRED = frozenset({"suspicious_collector", "layering"})
_ELEVATED = frozenset({"gambling", "exchange_nokyc", "bridge"})
_CLEAN = frozenset({"exchange_regulated", "payment_processor", "own_or_trusted"})


def group(category: str | None) -> str:
    if category is None:
        return "expanded"
    if category.startswith("untraced:"):
        return "untraced"
    if category in _HIGH:
        return "high"
    if category in _INFERRED:
        return "inferred"
    if category in _ELEVATED:
        return "elevated"
    if category in _CLEAN:
        return "clean"
    return "low"


def short(address: str) -> str:
    """First 8 and last 6 characters (methodology §4): enough to tell look-alikes apart."""
    return address if len(address) <= 17 else f"{address[:8]}…{address[-6:]}"


@dataclass(frozen=True)
class _Box:
    key: tuple[str, ...]  # path from the target, ending with this address
    node: Node
    x: int
    y: int


def _layout(trace: Trace) -> tuple[list[_Box], int, int]:
    hops = max((n.hop for n in trace.nodes), default=0)
    columns: dict[int, list[Node]] = {}
    for n in trace.nodes:
        columns.setdefault(n.hop, []).append(n)
    boxes = []
    rows = 0
    for hop, nodes in sorted(columns.items()):
        col = hops - hop if trace.direction == "in" else hop
        nodes.sort(key=lambda n: (-n.weight, n.address, n.path))
        rows = max(rows, len(nodes))
        for i, n in enumerate(nodes):
            x = MARGIN + col * COL_W
            y = TOP + i * (BOX_H + ROW_GAP)
            boxes.append(_Box((*n.path, n.address), n, x, y))
    width = MARGIN * 2 + hops * COL_W + BOX_W
    height = TOP + max(rows, 1) * (BOX_H + ROW_GAP)
    return boxes, width, height


def _edge_amounts(trace: Trace) -> dict[tuple[str, str], Decimal]:
    out: dict[tuple[str, str], Decimal] = {}
    for e in trace.edges:
        out[(e.sender, e.recipient)] = e.amount
    return out


def render(trace: Trace) -> str:
    boxes, width, height = _layout(trace)
    by_key = {b.key: b for b in boxes}
    amounts = _edge_amounts(trace)
    biggest = max(amounts.values(), default=Decimal(1)) or Decimal(1)
    legend = sorted(trace.partition.items(), key=lambda kv: (-kv[1], kv[0]))
    legend_h = MARGIN + LEGEND_ROW * (len(legend) + 2)
    total_h = height + legend_h

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{total_h}" '
        f'viewBox="0 0 {width} {total_h}" font-family="ui-sans-serif, system-ui, sans-serif">',
        f'<rect width="{width}" height="{total_h}" fill="#ffffff"/>',
    ]
    direction = "Source of funds" if trace.direction == "in" else "Destination of funds"
    coverage = pct(trace.coverage) if trace.coverage is not None else "no inflow"
    out.append(
        f'<text x="{MARGIN}" y="30" font-size="17" font-weight="600" fill="#111827">'
        f"{escape(direction)} · {trace.chain.value.upper()} · {escape(short(trace.target))}</text>"
    )
    sub = (
        f"as of {to_iso(trace.as_of)} · {dec(trace.target_inflow)} USDT · coverage {coverage}"
        f" · shares are proportional estimates"
        + ("" if trace.complete else " · INCOMPLETE: " + (trace.failure or "stopped early"))
    )
    out.append(f'<text x="{MARGIN}" y="52" font-size="12" fill="#4b5563">{escape(sub)}</text>')

    hops = max((n.hop for n in trace.nodes), default=0)
    for hop in range(hops + 1):
        col = hops - hop if trace.direction == "in" else hop
        head = "target" if hop == 0 else f"hop {hop}"
        out.append(
            f'<text x="{MARGIN + col * COL_W}" y="{TOP - 12}" font-size="11" '
            f'letter-spacing="0.5" fill="#6b7280">{head.upper()}</text>'
        )

    # Edges first, so boxes sit on top of them.
    for b in boxes:
        parent = by_key.get(b.node.path)
        if parent is None:
            continue
        sender, recipient = (
            (b.node.address, parent.node.address)
            if trace.direction == "in"
            else (parent.node.address, b.node.address)
        )
        amount = amounts.get((sender, recipient), b.node.weight * trace.target_inflow)
        left, right = (b, parent) if trace.direction == "in" else (parent, b)
        x1, y1 = left.x + BOX_W, left.y + BOX_H // 2
        x2, y2 = right.x, right.y + BOX_H // 2
        mid = (x1 + x2) // 2
        w = 1 + (MAX_EDGE_W - 1) * float(amount / biggest)
        colour = GROUPS[group(b.node.terminal) if b.node.terminal else "expanded"][1]
        out.append(
            f'<path d="M{x1},{y1} C{mid},{y1} {mid},{y2} {x2},{y2}" fill="none" '
            f'stroke="{colour}" stroke-opacity="0.45" stroke-width="{w:.1f}">'
            f"<title>{escape(f'{sender} → {recipient}: {dec(amount)} USDT')}</title></path>"
        )

    for b in boxes:
        n = b.node
        g = "target" if n.hop == 0 else group(n.terminal)
        fill, stroke, _ = GROUPS[g]
        dash = ' stroke-dasharray="4 3"' if g == "untraced" else ""
        what = "target" if n.hop == 0 else (n.terminal or "followed")
        cls = f" · inferred {n.classification.type}" if n.classification else ""
        tip = f"{n.address}\nhop {n.hop} · {what}{cls} · share {pct(n.weight)}"
        out.append(
            f"<g><title>{escape(tip)}</title>"
            f'<rect x="{b.x}" y="{b.y}" width="{BOX_W}" height="{BOX_H}" rx="6" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="1.5"{dash}/>'
            f'<rect x="{b.x}" y="{b.y}" width="6" height="{BOX_H}" rx="2" fill="{stroke}"/>'
            f'<text x="{b.x + 14}" y="{b.y + 19}" font-size="12.5" '
            f'font-family="ui-monospace, Menlo, Consolas, monospace" fill="#111827">'
            f"{escape(short(n.address))}</text>"
            f'<text x="{b.x + BOX_W - 8}" y="{b.y + 19}" font-size="11.5" font-weight="600" '
            f'text-anchor="end" fill="#111827">{pct(n.weight)}</text>'
            f'<text x="{b.x + 14}" y="{b.y + 36}" font-size="11" fill="#374151">'
            f"{escape(what)}</text></g>"
        )

    y = height + MARGIN
    out.append(
        f'<text x="{MARGIN}" y="{y}" font-size="13" font-weight="600" fill="#111827">'
        "Share of traced value</text>"
    )
    for i, (category, share) in enumerate(legend, start=1):
        fill, stroke, _ = GROUPS[group(category)]
        ly = y + i * LEGEND_ROW
        out.append(
            f'<rect x="{MARGIN}" y="{ly - 11}" width="14" height="14" rx="3" fill="{fill}" '
            f'stroke="{stroke}"/>'
            f'<text x="{MARGIN + 22}" y="{ly}" font-size="12" fill="#111827">'
            f"{escape(category)} · {pct(share)}</text>"
        )
    layering = trace.annotations.get("layering")
    if layering:
        ly = y + (len(legend) + 1) * LEGEND_ROW
        out.append(
            f'<text x="{MARGIN}" y="{ly}" font-size="12" fill="#b45309">'
            f"layering (peel chain, annotation): {pct(layering)}</text>"
        )
    out.append("</svg>")
    return "\n".join(out) + "\n"
