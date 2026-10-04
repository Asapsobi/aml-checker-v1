"""Score version 2: one number from 0 to 100 from the check's exposures and behaviour (methodology
§11.3, D-071). Version 1 scores (§9, D-052) stay stored with the checks v1 made and are read back
as they were, never recomputed.

- `H_dir` = Σ w × (1 − decay)^(hop − 1) × percent × confidence over a direction's exposures, at most
  1; `H` = 1 − (1 − H_in)(1 − H_out).
- Exposure points `X` = 100 × (1 − e^(−k·H)); behaviour points `B` from the behaviour rules, at most
  30. Both stored to 0.1, half up; the score is computed from the stored values, so a stored
  breakdown always reproduces its score.
- BLOCK ⇒ 100; otherwise min(99, ⌊X + (100 − X) × B / 100 + 0.5⌋).
- Levels: low 0–30, moderate 31–70, high 71–90, severe 91–100 (§11.4). INCOMPLETE ⇒ a lower bound,
  shown `≥ 34 · moderate+`.
- The score never makes a BLOCK; R-SCR-01 can make a REVIEW from `[score] review_at` (D-072).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Any

from amlcheck.chain.base import canonical_amount
from amlcheck.config import Score as ScoreSettings
from amlcheck.core.models import Finding, Verdict
from amlcheck.core.risk import DIRECTIONS, Exposure
from amlcheck.trace.model import dec

SCORE_VERSION = 2

#: §11.3 behaviour points. R-HEU-05's counterparties are direct exposures now, so it adds none.
BEHAVIOUR: dict[str, int] = {
    "R-HEU-01": 5,
    "R-HEU-02": 10,
    "R-HEU-03": 10,
    "R-HEU-04": 5,
    "R-HEU-06": 20,
    "R-HEU-07": 15,
    "R-FRZ-02": 15,
}
BEHAVIOUR_CAP = 30
LEVELS = ((91, "severe"), (71, "high"), (31, "moderate"), (0, "low"))
V1_BANDS = ((80, "severe"), (50, "high"), (20, "medium"), (0, "low"))  # §9, for v1 records
_TENTH = Decimal("0.1")


def level(score: int, version: int = SCORE_VERSION) -> str:
    """A score's level (§11.4), or a v1 score's band (§9)."""
    return next(name for floor, name in (V1_BANDS if version == 1 else LEVELS) if score >= floor)


def shown(score: int | None, verdict: str, version: int | None = SCORE_VERSION) -> str:
    """A stored score for lists: `66 · high`, `≥ 34 · moderate+` (INCOMPLETE), `-` (before P7).
    `version` None means a score whose version wasn't recorded: a v1 one (before P12)."""
    if score is None:
        return "-"
    name = level(score, version or 1)
    if verdict == Verdict.INCOMPLETE.value:
        return f"≥ {score} · {name}+"
    return f"{score} · {name}"


def _tenth(x: Decimal) -> Decimal:
    return x.quantize(_TENTH, rounding=ROUND_HALF_UP)


def hazards(exposures: Iterable[Exposure], decay: Decimal) -> dict[str, Decimal]:
    """`H_in`, `H_out`, each at most 1."""
    h = dict.fromkeys(DIRECTIONS, Decimal(0))
    for e in exposures:
        h[e.direction] += e.contribution(decay)
    return {d: min(v, Decimal(1)) for d, v in h.items()}


@dataclass(frozen=True)
class Score:
    score: int
    level: str
    lower_bound: bool  # INCOMPLETE: the true score is at least this
    exposure: Decimal  # X
    behaviour: Decimal  # B
    hazard_in: Decimal
    hazard_out: Decimal
    decay: Decimal
    k: Decimal
    version: int = SCORE_VERSION

    @property
    def band(self) -> str:
        """The level; the name lists and exports have used since v1."""
        return self.level

    @property
    def shown(self) -> str:
        """`66 · high`, or `≥ 34 · moderate+` for a lower bound."""
        return shown(self.score, Verdict.INCOMPLETE.value if self.lower_bound else "")

    @property
    def breakdown(self) -> str:
        return (
            f"exposure {canonical_amount(self.exposure)} · "
            f"behaviour {canonical_amount(self.behaviour)}"
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "score_version": self.version,
            "score": self.score,
            "level": self.level,
            "lower_bound": self.lower_bound,
            "components": {
                "X": canonical_amount(self.exposure),
                "B": canonical_amount(self.behaviour),
            },
            "hazard": {"in": dec(self.hazard_in), "out": dec(self.hazard_out)},
            "decay": canonical_amount(self.decay),
            "k": canonical_amount(self.k),
        }

    def dumps(self) -> str:
        """The `score_json` column: canonical, so the audit hash is stable (F6.4)."""
        return json.dumps(self.to_json(), sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class ScoreV1:
    """A v1 score as stored (§9, D-052), for checks made before v2. Read only."""

    score: int
    band: str
    lower_bound: bool
    stored: dict[str, Any]
    version: int = 1

    @property
    def level(self) -> str:
        return self.band

    @property
    def shown(self) -> str:
        return shown(self.score, Verdict.INCOMPLETE.value if self.lower_bound else "", 1)

    @property
    def breakdown(self) -> str:
        c = self.stored["components"]
        return " · ".join(f"{k} {c[k]}" for k in "EDBU")

    def to_json(self) -> dict[str, Any]:
        return dict(self.stored)


def compute(
    verdict: Verdict,
    findings: Iterable[Finding],
    exposures: Iterable[Exposure],
    settings: ScoreSettings,
) -> Score:
    """The score of a check from its exposures (§11.1) and behaviour findings."""
    rules = {f.rule_id for f in findings}
    h = hazards(exposures, settings.decay)
    combined = 1 - (1 - h["in"]) * (1 - h["out"])
    exposure = _tenth(100 * (1 - (-settings.k * combined).exp()))
    behaviour = _tenth(
        Decimal(min(BEHAVIOUR_CAP, sum(v for r, v in BEHAVIOUR.items() if r in rules)))
    )
    if verdict is Verdict.BLOCK:
        score = 100
    else:
        total = exposure + (100 - exposure) * behaviour / 100 + Decimal("0.5")
        score = min(99, int(total.to_integral_value(rounding=ROUND_FLOOR)))
    return Score(
        score=score,
        level=level(score),
        lower_bound=verdict is Verdict.INCOMPLETE,
        exposure=exposure,
        behaviour=behaviour,
        hazard_in=h["in"],
        hazard_out=h["out"],
        decay=settings.decay,
        k=settings.k,
    )


def decay_of(score: Score | ScoreV1 | None) -> Decimal:
    """The decay a stored score used, to rank its exposures; the default for a v1 record (which has
    no exposures anyway)."""
    return score.decay if isinstance(score, Score) else ScoreSettings().decay


def shown_stored(score_json: str | None) -> str:
    """A stored score as lists show it, read through its own JSON: a v1 score keeps its v1 band."""
    return from_json(score_json).shown if score_json else "-"


def from_json(text: str) -> Score | ScoreV1:
    d = json.loads(text)
    if d["score_version"] == 1:
        return ScoreV1(d["score"], d["band"], d["lower_bound"], d)
    c = d["components"]
    return Score(
        score=d["score"],
        level=d["level"],
        lower_bound=d["lower_bound"],
        exposure=Decimal(c["X"]),
        behaviour=Decimal(c["B"]),
        hazard_in=Decimal(d["hazard"]["in"]),
        hazard_out=Decimal(d["hazard"]["out"]),
        decay=Decimal(d["decay"]),
        k=Decimal(d["k"]),
        version=d["score_version"],
    )
