from datetime import UTC, datetime
from decimal import Decimal, localcontext

import pytest

from amlcheck.chain.base import Transfer, amount_from_units, canonical_amount, newest_first
from amlcheck.core.models import Chain


@pytest.mark.parametrize(
    ("units", "decimals", "text"),
    [
        (0, 6, "0"),
        (1, 6, "0.000001"),
        (1_500_000, 6, "1.5"),
        (10_933_000_000, 6, "10933"),
        (int("15f1ba7f471620000", 16), 18, "25.3"),  # VS-06 fixture log
        (10**18, 18, "1"),
        (123_456_789_012_345_678_901_234_567, 18, "123456789.012345678901234567"),
    ],
)
def test_amount_from_units_exact(units: int, decimals: int, text: str) -> None:
    assert canonical_amount(amount_from_units(units, decimals)) == text


def test_amount_exact_even_with_low_context_precision() -> None:
    with localcontext() as ctx:
        ctx.prec = 5
        assert canonical_amount(amount_from_units(123_456_789_123, 6)) == "123456.789123"


def test_same_amount_same_text_across_decimals() -> None:
    assert canonical_amount(amount_from_units(1_500_000, 6)) == canonical_amount(
        amount_from_units(1_500_000_000_000_000_000, 18)
    )
    assert canonical_amount(Decimal("1E+2")) == "100"


def test_negative_refused() -> None:
    with pytest.raises(ValueError, match="negative"):
        amount_from_units(-1, 6)


def t(time: int, tx: str, idx: int = 0, block: int | None = None) -> Transfer:
    return Transfer(
        Chain.BSC, tx, idx, block, datetime.fromtimestamp(time, UTC), "0xa", "0xb", Decimal(1)
    )


def test_newest_first_is_deterministic() -> None:
    items = [t(100, "0xb"), t(200, "0xa"), t(100, "0xa", 1), t(100, "0xa", 0)]
    expected = [t(200, "0xa"), t(100, "0xa", 1), t(100, "0xa", 0), t(100, "0xb")]
    assert list(newest_first(items)) == expected
    assert list(newest_first(reversed(items))) == expected
