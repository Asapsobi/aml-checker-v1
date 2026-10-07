"""Risk policy, version 2: exposures, risk types and weights (methodology §11, D-071).

An exposure is money the checked address exchanged with a risk category: directly (hop 1, a
counterparty, exact amounts from its history) or through others (hop ≥ 2, the trace's proportional
estimate, D-016). The score (§11.3) is built from these and nothing else but behaviour points.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

from amlcheck.chain.base import canonical_amount
from amlcheck.intel.categories import BY_NAME, LABELS_CSV_CATEGORY
from amlcheck.trace.model import Trace, dec, pct

RISK_VERSION = 2

#: §11.2: category → (risk type, weight). A category that isn't here is not a risk (weight 0):
#: an unknown service is not a risk in itself (D-071).
RISK: dict[str, tuple[str, Decimal]] = {
    "sanctioned": ("sanctioned_entity", Decimal("1.0")),
    "frozen": ("frozen", Decimal("0.9")),
    "stolen_funds": ("illicit_activity", Decimal("0.9")),
    "darknet": ("illicit_activity", Decimal("0.9")),
    "mixer": ("mixer", Decimal("0.8")),
    "scam": ("illicit_activity", Decimal("0.7")),
    "high_risk": ("illicit_activity", Decimal("0.6")),
    "suspicious_collector": ("illicit_activity", Decimal("0.5")),
    "suspected_malicious": ("illicit_activity", Decimal("0.6")),  # §13.2, × confidence
    "gambling": ("gambling", Decimal("0.3")),
    "exchange_nokyc": ("risk_exchange", Decimal("0.3")),
    "bridge": ("bridge", Decimal("0.2")),
}
#: Display order, most severe first.
RISK_TYPES = (
    "sanctioned_entity",
    "frozen",
    "illicit_activity",
    "mixer",
    "gambling",
    "risk_exchange",
    "bridge",
)
DIRECTIONS = ("in", "out")


@dataclass(frozen=True)
class Exposure:
    direction: str  # "in": money the address received; "out": money it sent
    hop: int  # 1 = a counterparty
    address: str  # the risk end
    category: str  # §8, one of RISK
    entity: str  # who it is, for people
    volume: Decimal  # USDT; exact when direct, an estimate when indirect (D-016)
    percent: Decimal  # share of the address's flow in that direction, 0–1
    path: tuple[str, ...] = ()  # the checked address first, the risk end last
    confidence: Decimal | None = None  # inferred categories only
    estimate: Decimal | None = None  # indirect only: the proportional estimate (D-016, D-078)

    @property
    def exposure_type(self) -> str:
        return "direct" if self.hop == 1 else "indirect"

    @property
    def risk_type(self) -> str:
        return RISK[self.category][0]

    @property
    def inferred(self) -> bool:
        return self.confidence is not None

    def contribution(self, decay: Decimal, weights: Mapping[str, Decimal] | None = None) -> Decimal:
        """`c(e) = w × (1 − decay)^(hop − 1) × percent × confidence` (§11.3). `weights` replaces
        some categories' `w`: the benchmark tries candidates offline that way (§14.3)."""
        w = (
            weights[self.category]
            if weights and self.category in weights
            else RISK[self.category][1]
        )
        c = w * (1 - decay) ** (self.hop - 1) * self.percent
        return c * self.confidence if self.confidence is not None else c

    def to_json(self) -> dict[str, Any]:
        return {
            "direction": self.direction,
            "exposure_type": self.exposure_type,
            "hop": self.hop,
            "address": self.address,
            "entity": self.entity,
            "category": self.category,
            "risk_type": self.risk_type,
            "volume_usdt": canonical_amount(self.volume) if self.hop == 1 else dec(self.volume),
            "percent": dec(self.percent),
            "inferred": self.inferred,
            "confidence": dec(self.confidence) if self.confidence is not None else None,
            "path": list(self.path),
            "estimated_usdt": dec(self.estimate) if self.estimate is not None else None,
        }

    @classmethod
    def from_json(cls, d: Mapping[str, Any]) -> Exposure:
        return cls(
            direction=d["direction"],
            hop=d["hop"],
            address=d["address"],
            category=d["category"],
            entity=d["entity"],
            volume=Decimal(d["volume_usdt"]),
            percent=Decimal(d["percent"]),
            path=tuple(d["path"]),
            confidence=Decimal(d["confidence"]) if d.get("confidence") is not None else None,
            estimate=Decimal(d["estimated_usdt"]) if d.get("estimated_usdt") is not None else None,
        )


def ordered(exposures: Iterable[Exposure]) -> list[Exposure]:
    """Stable order for records and display: direction, then heaviest first (weight × percent,
    before decay, so the order doesn't depend on config), then hop, address, category."""
    return sorted(
        exposures,
        key=lambda e: (
            DIRECTIONS.index(e.direction),
            -RISK[e.category][1] * e.percent * (e.confidence or 1),
            e.hop,
            e.address,
            e.category,
            e.path,
        ),
    )


#: The node class a trace gives a freeze neighbour (§13.2, trace engine).
SUSPECTED_CLASS = "SUSPECTED_MALICIOUS"
#: Terminal tests that read local flags (§7.5 tests 2–4): on hop 1 those counterparties are already
#: direct exposures, from exact amounts.
_LOCAL_TESTS = (2, 3, 4)


#: The entity named for what sits behind a busy service (D-097).
BEHIND = "behind a busy service"


def from_trace(
    trace: Trace, name: Callable[[str, str], str], method: str = "path"
) -> list[Exposure]:
    """Indirect exposures (§11.1, D-078): the trace's risk terminals at hop ≥ 2, plus hop-1
    terminals a classification decided (tests 5, 8, 9), which local flags can't see.

    `method` "path": the volume is the path's bottleneck, the smallest edge on it, which every hop
    moved (what MistTrack-style exposure reports); the exposures reached through one first-hop
    counterparty are capped together at what that counterparty itself sent or received, so two
    paths through it never count its money twice. `method` "proportional": the volume is the
    trace's proportional estimate `weight × flow` (D-016). Either way the estimate is kept too. An
    inferred category carries the confidence of the classification behind it."""
    total = trace.target_inflow
    if not total:
        return []
    found: list[tuple[str, Exposure]] = []
    for n in trace.nodes:
        cls = n.classification
        if n.terminal in RISK and not (n.hop == 1 and n.test in _LOCAL_TESTS):
            category = n.terminal  # a risk end (hop-1 local facts are direct exposures already)
        elif cls is not None and cls.type == SUSPECTED_CLASS and n.terminal not in RISK:
            category = "suspected_malicious"  # a freeze neighbour the trace went through (§13.2)
        else:
            continue
        inferred = BY_NAME[category].provenances == frozenset({"inferred"})
        estimate = n.weight * total
        volume = n.bottleneck if method == "path" and n.bottleneck is not None else estimate
        path = (*n.path, n.address)
        found.append(
            (
                path[1],
                Exposure(
                    trace.direction,
                    n.hop,
                    n.address,
                    category,
                    cls.type if inferred and cls else name(n.address, category),
                    volume,
                    volume / total,
                    path,
                    (cls.confidence if cls else Decimal(1)) if inferred else None,
                    estimate,
                ),
            )
        )
    # D-097: what sits behind a busy service the trace stopped at, one hop beyond it, inferred
    # at the pass-through weight. Traces recorded without that setting give none.
    through = Decimal(str(trace.settings.get("service_pass_through", 0)))
    for n in trace.nodes if through else ():
        for category, share in n.behind:
            estimate = n.weight * total * share
            base = (
                n.bottleneck if method == "path" and n.bottleneck is not None else n.weight * total
            )
            volume = base * share
            path = (*n.path, n.address)
            found.append(
                (
                    path[1],
                    Exposure(
                        trace.direction,
                        n.hop + 1,
                        n.address,
                        category,
                        BEHIND,
                        volume,
                        volume / total,
                        path,
                        through,
                        estimate,
                    ),
                )
            )
    return ordered(_capped(trace, found) if method == "path" else [e for _, e in found])


def _capped(trace: Trace, found: list[tuple[str, Exposure]]) -> list[Exposure]:
    """Scale the exposures reached through each first-hop counterparty down to its own edge."""
    edge: dict[str, Decimal] = {}
    for ed in trace.edges:
        other = ed.sender if ed.recipient == trace.target else ed.recipient
        if trace.target in (ed.sender, ed.recipient):
            edge[other] = edge.get(other, Decimal(0)) + ed.amount
    by_first: dict[str, list[Exposure]] = {}
    for first, e in found:
        by_first.setdefault(first, []).append(e)
    out: list[Exposure] = []
    for first, group in by_first.items():
        cap = edge.get(first)
        volume = sum((e.volume for e in group), Decimal(0))
        if cap is None or volume <= cap:
            out += group
            continue
        out += [
            replace(
                e,
                volume=e.volume * cap / volume,
                percent=e.volume * cap / volume / trace.target_inflow,
            )
            for e in group
        ]
    return out


def flag_category(flag: str) -> str | None:
    """A §3.3 counterparty flag as a §8 category: `sanctioned`, `frozen`, or `label:<tag>` where the
    tag is a labels.csv tag or an intelligence-label category. Free-text tags have none."""
    if flag in ("sanctioned", "frozen"):
        return flag
    tag = flag.removeprefix("label:")
    category = LABELS_CSV_CATEGORY.get(tag, tag)
    return category if category in BY_NAME else None


def risk_category(flags: Iterable[str]) -> str | None:
    """The most severe risk category among a counterparty's flags (§8 order), if any is a risk.
    An allowlist tag never hides a sanctions or freeze flag (F5.4)."""
    found = [c for c in (flag_category(f) for f in flags) if c is not None and c in RISK]
    return min(found, key=lambda c: BY_NAME[c].order, default=None)


#: One plain line per risk type (methodology §11.1, D-073).
TITLES = {
    "sanctioned_entity": "Sanctioned entity",
    "frozen": "Tether-frozen address",
    "illicit_activity": "Illicit activity",
    "mixer": "Mixer",
    "gambling": "Gambling",
    "risk_exchange": "High-risk exchange",
    "bridge": "Bridge",
}


def from_evidence(evidences: Iterable[Mapping[str, Any]]) -> list[Exposure]:
    """Every exposure a check's sources recorded (the exposure source's direct ones, the trace's
    indirect ones). Records from before P12 have none."""
    return [Exposure.from_json(e) for ev in evidences for e in ev.get("exposures", ())]


def ranked(exposures: Iterable[Exposure], decay: Decimal) -> list[Exposure]:
    """Heaviest first by what each adds to the score (§11.3); ties broken fully (CLAUDE.md #6)."""
    return sorted(
        exposures,
        key=lambda e: (
            -e.contribution(decay),
            DIRECTIONS.index(e.direction),
            e.hop,
            e.address,
            e.category,
            e.path,
        ),
    )


def detail_list(exposures: Iterable[Exposure]) -> list[str]:
    """`Sanctioned entity: direct received 5.0%, indirect received 0.8%`, one line per risk type in
    severity order; `(inferred)` when every exposure of that type is an inference."""
    by_type: dict[str, list[Exposure]] = {}
    for e in exposures:
        by_type.setdefault(e.risk_type, []).append(e)
    lines = []
    for risk_type in RISK_TYPES:
        found = by_type.get(risk_type)
        if not found:
            continue
        parts = []
        for direction, word in (("in", "received"), ("out", "sent")):
            for kind in ("direct", "indirect"):
                share = sum(
                    (
                        e.percent
                        for e in found
                        if (e.direction, e.exposure_type) == (direction, kind)
                    ),
                    Decimal(0),
                )
                if share:
                    parts.append(f"{kind} {word} {pct(share)}")
        inferred = " (inferred)" if all(e.inferred for e in found) else ""
        lines.append(f"{TITLES[risk_type]}: {', '.join(parts)}{inferred}")
    return lines
