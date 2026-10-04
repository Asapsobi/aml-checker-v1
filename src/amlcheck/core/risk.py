"""Risk policy, version 2: exposures, risk types and weights (methodology §11, D-071).

An exposure is money the checked address exchanged with a risk category: directly (hop 1, a
counterparty, exact amounts from its history) or through others (hop ≥ 2, the trace's proportional
estimate, D-016). The score (§11.3) is built from these and nothing else but behaviour points.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from amlcheck.chain.base import canonical_amount
from amlcheck.intel.categories import BY_NAME, LABELS_CSV_CATEGORY
from amlcheck.trace.model import Trace, dec

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

    @property
    def exposure_type(self) -> str:
        return "direct" if self.hop == 1 else "indirect"

    @property
    def risk_type(self) -> str:
        return RISK[self.category][0]

    @property
    def inferred(self) -> bool:
        return self.confidence is not None

    def contribution(self, decay: Decimal) -> Decimal:
        """`c(e) = w × (1 − decay)^(hop − 1) × percent × confidence` (§11.3)."""
        c = RISK[self.category][1] * (1 - decay) ** (self.hop - 1) * self.percent
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


#: Terminal tests that read local flags (§7.5 tests 2–4): on hop 1 those counterparties are already
#: direct exposures, from exact amounts.
_LOCAL_TESTS = (2, 3, 4)


def from_trace(trace: Trace, name: Callable[[str, str], str]) -> list[Exposure]:
    """Indirect exposures (§11.1): the trace's risk terminals at hop ≥ 2, plus hop-1 terminals a
    classification decided (tests 5, 8, 9), which local flags can't see. Volume is the
    proportional estimate `weight × flow` (D-016); an inferred category carries the confidence of
    the classification behind it. `name(address, category)` names the risk end."""
    out: list[Exposure] = []
    for n in trace.nodes:
        if n.terminal not in RISK or (n.hop == 1 and n.test in _LOCAL_TESTS):
            continue
        inferred = BY_NAME[n.terminal].provenances == frozenset({"inferred"})
        cls = n.classification
        out.append(
            Exposure(
                trace.direction,
                n.hop,
                n.address,
                n.terminal,
                f"{cls.type} (inferred)" if inferred and cls else name(n.address, n.terminal),
                n.weight * trace.target_inflow,
                n.weight,
                (*n.path, n.address),
                (cls.confidence if cls else Decimal(1)) if inferred else None,
            )
        )
    return ordered(out)


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
