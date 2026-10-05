from decimal import Decimal

import pytest

from amlcheck.intel.categories import (
    BY_NAME,
    CATEGORIES,
    CATEGORY_VERSION,
    LABELS_CSV_CATEGORY,
    UnknownCategory,
    check_assignable,
    get,
)


def test_table_matches_methodology_section_8() -> None:
    assert CATEGORY_VERSION == 2  # P14: suspected_malicious appended (§13.2)
    assert [c.name for c in CATEGORIES][:3] == ["sanctioned", "frozen", "stolen_funds"]
    assert [c.order for c in CATEGORIES] == list(range(1, 19))
    assert CATEGORIES[-1].name == "suspected_malicious"
    assert {c.name for c in CATEGORIES if c.high_risk} == {
        "sanctioned",
        "frozen",
        "stolen_funds",
        "darknet",
        "mixer",
        "scam",
        "high_risk",
    }
    assert get("mixer").weight == Decimal("0.8")
    assert get("exchange_regulated").weight == 0
    kinds = {c.name for c in CATEGORIES if c.entity_kind}
    assert "sanctioned" not in kinds
    assert {"stolen_funds", "own_or_trusted", "exchange_regulated"} <= kinds


def test_unknown_category_refused() -> None:
    with pytest.raises(UnknownCategory, match="unknown category 'casino'"):
        get("casino")


@pytest.mark.parametrize(
    ("name", "prov", "ok"),
    [
        ("mixer", "operator", True),
        ("mixer", "import", True),
        ("sanctioned", "operator", False),  # only the list
        ("sanctioned", "list", True),
        ("suspicious_collector", "operator", False),  # only inferred
        ("own_or_trusted", "inferred", False),
    ],
)
def test_who_may_assign(name: str, prov: str, ok: bool) -> None:
    if ok:
        assert check_assignable(name, prov) is BY_NAME[name]
    else:
        with pytest.raises(ValueError, match="can only come from"):
            check_assignable(name, prov)


def test_labels_csv_mapping_points_at_real_categories() -> None:
    assert all(v in BY_NAME for v in LABELS_CSV_CATEGORY.values())
    assert LABELS_CSV_CATEGORY["allowlist"] == "own_or_trusted"
