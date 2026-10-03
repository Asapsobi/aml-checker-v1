"""R-TRC-01…05 from a finished inbound trace (PRD §7.2, methodology §7.1).

- R-TRC-01 / 02: a path reaches a sanctioned / frozen wallet at hop 2–3 with a bottleneck ≥
  `min_flagged_usdt`: an absolute amount, not an estimate (D-016). REVIEW by default (D-044).
- R-TRC-03: estimated exposure to the high-risk categories (methodology §8) ≥ `high_risk_share`.
- R-TRC-04: coverage below `min_coverage` (low priority).
- R-TRC-05: estimated exposure to inferred suspicious patterns (`suspicious_collector` + `layering`)
  ≥ `inferred_share` (low priority, never BLOCK, D-017).

Hop-1 senders that ended at the local tests 2–4 are R-EXP-01's territory: they keep their share of
the partition but raise no R-TRC finding (methodology §7.5). Forward traces and traces of an address
without inflow raise none.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from amlcheck.config import Trace as TraceSettings
from amlcheck.core.models import Finding
from amlcheck.core.rules import finding
from amlcheck.intel.categories import CATEGORIES
from amlcheck.trace.engine import hop1_local
from amlcheck.trace.model import Trace, TracePath, dec

SOURCE = "trace"
HIGH_RISK = frozenset(c.name for c in CATEGORIES if c.high_risk)
INFERRED_SUSPICIOUS = ("suspicious_collector",)


def _path(p: TracePath) -> dict[str, Any]:
    return {
        "addresses": list(p.addresses),
        "hops": p.hops,
        "bottleneck_usdt": dec(p.bottleneck),
        "estimated_usdt": dec(p.estimated),
    }


def _pct(x: Decimal) -> str:
    return f"{(x * 100).quantize(Decimal('0.1'))}%"


def trace_findings(trace: Trace, s: TraceSettings, now: datetime) -> list[Finding]:
    if trace.direction != "in" or trace.coverage is None:
        return []
    out: list[Finding] = []
    for rule, category, word in (
        ("R-TRC-01", "sanctioned", "sanctioned"),
        ("R-TRC-02", "frozen", "frozen"),
    ):
        paths = sorted(
            (
                p
                for p in trace.paths
                if p.to_category == category and p.hops >= 2 and p.bottleneck >= s.min_flagged_usdt
            ),
            key=lambda p: (-p.bottleneck, p.addresses),
        )
        if paths:
            best = paths[0]
            out.append(
                finding(
                    rule,
                    SOURCE,
                    f"Funds trace back to a {word} wallet {best.hops} hops away "
                    f"(at least {dec(best.bottleneck)} USDT along the path)",
                    now,
                    {
                        "paths": [_path(p) for p in paths[:5]],
                        "min_flagged_usdt": dec(s.min_flagged_usdt),
                    },
                )
            )
    local = hop1_local(trace)
    high = {
        c: v - local.get(c, Decimal(0))
        for c, v in trace.partition.items()
        if c in HIGH_RISK and v - local.get(c, Decimal(0)) > 0
    }
    high_share = sum(high.values(), Decimal(0))
    if high_share >= s.high_risk_share:
        out.append(
            finding(
                "R-TRC-03",
                SOURCE,
                f"An estimated {_pct(high_share)} of the inflow traces back to high-risk sources "
                f"({', '.join(sorted(high))})",
                now,
                {
                    "share": dec(high_share),
                    "by_category": {k: dec(v) for k, v in sorted(high.items())},
                    "estimated": True,
                },
            )
        )
    if trace.coverage < s.min_coverage:
        out.append(
            finding(
                "R-TRC-04",
                SOURCE,
                f"Only {_pct(trace.coverage)} of the inflow could be attributed",
                now,
                {"priority": "low", "coverage": dec(trace.coverage)},
            )
        )
    inferred = sum((trace.partition.get(c, Decimal(0)) for c in INFERRED_SUSPICIOUS), Decimal(0))
    inferred += trace.annotations.get("layering", Decimal(0))
    if inferred >= s.inferred_share:
        out.append(
            finding(
                "R-TRC-05",
                SOURCE,
                f"An estimated {_pct(inferred)} of the inflow passed through inferred suspicious "
                "patterns (collectors, layering)",
                now,
                {
                    "priority": "low",
                    "share": dec(inferred),
                    "suspicious_collector": dec(
                        trace.partition.get("suspicious_collector", Decimal(0))
                    ),
                    "layering": dec(trace.annotations.get("layering", Decimal(0))),
                    "inferred": True,
                },
            )
        )
    return out
