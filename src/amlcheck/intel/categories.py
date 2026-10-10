"""Categories, version 1 (methodology §8). Imports nothing from the project (architecture §2).

Order matters: it breaks ties when picking an address's terminal category (methodology §7.5 test 4).
A change to this table bumps `CATEGORY_VERSION`; labels store the version they were made under.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

CATEGORY_VERSION = 3  # 2.1: sanctioned_entity (methodology §13.4)

PROVENANCES = frozenset({"list", "operator", "import", "inferred"})
_HUMAN = frozenset({"operator", "import"})


class UnknownCategory(ValueError):
    pass


@dataclass(frozen=True)
class Category:
    name: str
    order: int  # 1 = most severe; breaks ties (methodology §7.5)
    weight: Decimal  # score weight (methodology §9)
    provenances: frozenset[str]  # who may assign it
    high_risk: bool  # in R-TRC-03's high-risk set

    @property
    def entity_kind(self) -> bool:
        """Categories 3–17 may be an entity's kind (methodology §8)."""
        return 3 <= self.order <= 17


CATEGORIES: tuple[Category, ...] = (
    Category("sanctioned", 1, Decimal("1.0"), frozenset({"list"}), True),
    Category("frozen", 2, Decimal("0.9"), frozenset({"list"}), True),
    Category("stolen_funds", 3, Decimal("0.9"), _HUMAN, True),
    Category("darknet", 4, Decimal("0.9"), _HUMAN, True),
    Category("mixer", 5, Decimal("0.8"), _HUMAN, True),
    Category("scam", 6, Decimal("0.7"), _HUMAN, True),
    Category("high_risk", 7, Decimal("0.6"), _HUMAN, True),
    Category("suspicious_collector", 8, Decimal("0.5"), frozenset({"inferred"}), False),
    Category("gambling", 9, Decimal("0.3"), _HUMAN, False),
    Category("exchange_nokyc", 10, Decimal("0.3"), _HUMAN, False),
    Category("bridge", 11, Decimal("0.2"), _HUMAN, False),
    Category("otc_desk", 12, Decimal("0.1"), _HUMAN, False),
    Category("service_unattributed", 13, Decimal("0.1"), frozenset({"inferred"}), False),
    Category("contract_unattributed", 14, Decimal("0.1"), frozenset({"inferred"}), False),
    Category("exchange_regulated", 15, Decimal("0.0"), _HUMAN, False),
    Category("payment_processor", 16, Decimal("0.0"), _HUMAN, False),
    Category("own_or_trusted", 17, Decimal("0.0"), _HUMAN, False),
    # P14 (D-086): a freeze neighbour, inferred from a trace read; last, so no tie order moves.
    Category("suspected_malicious", 18, Decimal("0.6"), frozenset({"inferred"}), False),
    # 2.1 (D-100): a wallet of an entity a sanctions list designates, attributed by its public tag
    # or by the operator (§13.4). Not the list's own address (that is `sanctioned`); last again.
    Category("sanctioned_entity", 19, Decimal("0.9"), _HUMAN, True),
)
BY_NAME = {c.name: c for c in CATEGORIES}

#: Trace annotation only, not a terminal category (methodology §6.1, §8).
LAYERING_WEIGHT = Decimal("0.5")

#: labels.csv tags that carry a category (PRD F5.3, methodology §8). Other tags stay free text.
LABELS_CSV_CATEGORY = {
    "mixer": "mixer",
    "bridge": "bridge",
    "high_risk": "high_risk",
    "allowlist": "own_or_trusted",
}


def get(name: str) -> Category:
    try:
        return BY_NAME[name]
    except KeyError:
        known = ", ".join(c.name for c in CATEGORIES)
        raise UnknownCategory(f"unknown category {name!r}; one of: {known}") from None


def check_assignable(name: str, provenance: str) -> Category:
    """The category, if `provenance` may assign it (e.g. an operator can't label `sanctioned`)."""
    if provenance not in PROVENANCES:
        raise ValueError(f"unknown provenance {provenance!r}")
    category = get(name)
    if provenance not in category.provenances:
        allowed = ", ".join(sorted(category.provenances))
        raise ValueError(f"{name} can only come from: {allowed}")
    return category
