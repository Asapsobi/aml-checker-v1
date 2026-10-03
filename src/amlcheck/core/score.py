"""Score version 1: one number from 0 to 100 with its breakdown (methodology §9, PRD F10, D-052).

- Hazard `H` = Σ weight × share over the trace's terminals; an inferred category also multiplies by
  its terminal's confidence (share-weighted, D-052), plus 0.5 × the layering annotation. 0–1.
- Exposure `E` = 60 × (1 − e^(−20·H)); direct `D` from R-EXP-01/02, capped at 30; behaviour `B` from
  the behaviour rules, capped at 30; uncertainty `U` = 10 × (1 − coverage) for a complete trace.
- Each component is stored to 0.1, half up, and the score is computed from the stored values, so a
  stored breakdown always reproduces its score (D-052). BLOCK ⇒ 100; otherwise
  min(99, ⌊E + D + B + U + 0.5⌋).
- INCOMPLETE ⇒ the score is a lower bound, shown `≥ 34` with the band `medium+` (F10.1).
- The score never changes the verdict; only R-SCR-01, when the owner turns it on, can (D-051).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Any

from amlcheck.chain.base import canonical_amount
from amlcheck.core.models import Finding, Verdict
from amlcheck.intel.categories import BY_NAME, CATEGORIES
from amlcheck.trace.model import Trace

SCORE_VERSION = 1

#: Inferred-only categories (methodology §8): their share counts at their terminal's confidence.
INFERRED = frozenset(c.name for c in CATEGORIES if c.provenances == frozenset({"inferred"}))
LAYERING_WEIGHT = Decimal("0.5")
DIRECT: dict[str, int] = {"R-EXP-01": 25, "R-EXP-02": 15}
BEHAVIOUR: dict[str, int] = {
    "R-HEU-01": 5,
    "R-HEU-02": 10,
    "R-HEU-03": 10,
    "R-HEU-04": 5,
    "R-HEU-05": 15,
    "R-HEU-06": 20,
    "R-HEU-07": 15,
    "R-FRZ-02": 15,
}
CAP = 30
BANDS = ((80, "severe"), (50, "high"), (20, "medium"), (0, "low"))
_TENTH = Decimal("0.1")


def hazard(trace: Trace) -> Decimal:
    """`H` from the trace's terminals. A partial trace counts what it attributed (D-052)."""
    h = Decimal(0)
    for n in trace.nodes:
        if n.terminal is None or n.terminal.startswith("untraced:"):
            continue
        category = BY_NAME[n.terminal]
        part = category.weight * n.weight
        if category.name in INFERRED and n.classification is not None:
            part *= n.classification.confidence
        h += part
    h += LAYERING_WEIGHT * trace.annotations.get("layering", Decimal(0))
    return min(h, Decimal(1))


def band(score: int) -> str:
    return next(name for floor, name in BANDS if score >= floor)


def shown(score: int | None, verdict: str) -> str:
    """A stored score for lists: `66 · high`, `≥ 34 · medium+` (INCOMPLETE), `-` (before P7)."""
    if score is None:
        return "-"
    if verdict == Verdict.INCOMPLETE.value:
        return f"≥ {score} · {band(score)}+"
    return f"{score} · {band(score)}"


def _tenth(x: Decimal) -> Decimal:
    return x.quantize(_TENTH, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class Score:
    score: int
    band: str
    lower_bound: bool  # INCOMPLETE: the true score is at least this
    exposure: Decimal  # E
    direct: Decimal  # D
    behaviour: Decimal  # B
    uncertainty: Decimal  # U
    hazard: Decimal | None  # None when no trace ran
    version: int = SCORE_VERSION

    @property
    def shown(self) -> str:
        """`66 · high`, or `≥ 34 · medium+` for a lower bound."""
        return shown(self.score, Verdict.INCOMPLETE.value if self.lower_bound else "")

    @property
    def breakdown(self) -> str:
        parts = zip(
            "EDBU", (self.exposure, self.direct, self.behaviour, self.uncertainty), strict=True
        )
        return " · ".join(f"{k} {canonical_amount(v)}" for k, v in parts)

    def to_json(self) -> dict[str, Any]:
        return {
            "score_version": self.version,
            "score": self.score,
            "band": self.band,
            "lower_bound": self.lower_bound,
            "components": {
                "E": canonical_amount(self.exposure),
                "D": canonical_amount(self.direct),
                "B": canonical_amount(self.behaviour),
                "U": canonical_amount(self.uncertainty),
            },
            "hazard": canonical_amount(self.hazard) if self.hazard is not None else None,
        }

    def dumps(self) -> str:
        """The `score_json` column: canonical, so the audit hash is stable (F6.4)."""
        return json.dumps(self.to_json(), sort_keys=True, separators=(",", ":"))


def compute(
    verdict: Verdict, findings: Iterable[Finding], trace: Mapping[str, Any] | None = None
) -> Score:
    """The score of a check. `trace` is the trace source's evidence, when a trace ran."""
    rules = {f.rule_id for f in findings}
    h: Decimal | None = None
    exposure = uncertainty = Decimal(0)
    if trace and trace.get("hazard") is not None:
        h = Decimal(str(trace["hazard"]))
        exposure = _tenth(60 * (1 - (-20 * h).exp()))
        coverage = trace.get("coverage")
        if trace.get("complete") and coverage is not None:  # no inflow or partial: 0 (D-052)
            uncertainty = _tenth(10 * (1 - Decimal(str(coverage))))
    direct = Decimal(min(CAP, sum(v for r, v in DIRECT.items() if r in rules)))
    behaviour = Decimal(min(CAP, sum(v for r, v in BEHAVIOUR.items() if r in rules)))
    if verdict is Verdict.BLOCK:
        score = 100
    else:
        total = exposure + direct + behaviour + uncertainty + Decimal("0.5")
        score = min(99, int(total.to_integral_value(rounding=ROUND_FLOOR)))
    return Score(
        score=score,
        band=band(score),
        lower_bound=verdict is Verdict.INCOMPLETE,
        exposure=exposure,
        direct=direct,
        behaviour=behaviour,
        uncertainty=uncertainty,
        hazard=h,
    )


def from_json(text: str) -> Score:
    d = json.loads(text)
    c = d["components"]
    return Score(
        score=d["score"],
        band=d["band"],
        lower_bound=d["lower_bound"],
        exposure=Decimal(c["E"]),
        direct=Decimal(c["D"]),
        behaviour=Decimal(c["B"]),
        uncertainty=Decimal(c["U"]),
        hazard=Decimal(d["hazard"]) if d.get("hazard") is not None else None,
        version=d["score_version"],
    )
