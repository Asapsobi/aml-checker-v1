from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from amlcheck.chain.base import Transfer
from amlcheck.core.models import Chain
from amlcheck.screening.heuristics import busiest_window, pass_through, recipients, senders

ME = "TME"
T0 = datetime(2026, 9, 1, tzinfo=UTC)
DAY = timedelta(hours=24)


def t(hours: float, amount: str, *, out: bool = False, other: str = "TX", tx: str = "") -> Transfer:
    return Transfer(
        Chain.TRON,
        tx or f"tx{hours}{amount}{out}{other}",
        0,
        None,
        T0 + timedelta(hours=hours),
        ME if out else other,
        other if out else ME,
        Decimal(amount),
    )


def test_pass_through_basic() -> None:
    p = pass_through(ME, [t(0, "1000"), t(2, "950", out=True)], DAY)
    assert (p.received, p.passed) == (Decimal(1000), Decimal(950))
    assert p.share == Decimal("0.95")


def test_pass_through_window_boundary() -> None:
    at = pass_through(ME, [t(0, "100"), t(24, "100", out=True)], DAY)
    past = pass_through(ME, [t(0, "100"), t(24.001, "100", out=True)], DAY)
    assert at.passed == Decimal(100)
    assert past.passed == Decimal(0)


def test_pass_through_fifo_spends_oldest_first() -> None:
    # 100 arrives day 0, 100 arrives day 2; 150 leaves day 2.5: 100 old (not within 24 h) + 50 new.
    p = pass_through(ME, [t(0, "100"), t(48, "100"), t(60, "150", out=True)], DAY)
    assert p.passed == Decimal(50)


def test_money_held_before_history_is_not_counted() -> None:
    p = pass_through(ME, [t(0, "500", out=True), t(1, "100"), t(2, "100", out=True)], DAY)
    assert (p.received, p.passed) == (Decimal(100), Decimal(100))


def test_same_instant_arrival_counts_before_leaving() -> None:
    p = pass_through(ME, [t(5, "70", out=True, tx="a"), t(5, "70", tx="b")], DAY)
    assert p.passed == Decimal(70)


def test_self_transfers_and_no_inflow() -> None:
    selfie = Transfer(Chain.TRON, "s", 0, None, T0, ME, ME, Decimal(5))
    p = pass_through(ME, [selfie, t(1, "5", out=True)], DAY)
    assert p.received == 0
    assert p.share is None


@given(st.lists(st.tuples(st.integers(0, 500), st.integers(1, 10_000), st.booleans()), max_size=60))
def test_pass_through_never_exceeds_received(rows: list[tuple[int, int, bool]]) -> None:
    p = pass_through(ME, [t(h, str(a), out=o, tx=f"{i}") for i, (h, a, o) in enumerate(rows)], DAY)
    assert 0 <= p.passed <= p.received


def test_busiest_window_distinct_and_inclusive() -> None:
    ev = [
        (T0 + timedelta(hours=h), p)
        for h, p in [(0, "a"), (1, "b"), (1, "b"), (24, "c"), (25, "d")]
    ]
    n, start = busiest_window(ev, DAY)
    assert n == 3  # a, b, c within [0 h, 24 h]; then b, c, d within [1 h, 25 h]
    assert start == T0
    assert busiest_window([], DAY) == (0, None)


@pytest.mark.parametrize(("n", "spread_h", "want"), [(60, 3, 60), (60, 48, 30)])
def test_busiest_window_fan(n: int, spread_h: float, want: int) -> None:
    step = timedelta(hours=spread_h) / (n - 1)
    ev = [(T0 + i * step, f"s{i}") for i in range(n)]
    assert busiest_window(ev, DAY)[0] == want


def test_senders_and_recipients_filters() -> None:
    xs = [t(0, "20", other="A"), t(1, "500", other="B"), t(2, "5", out=True, other="C")]
    assert [p for _, p in senders(ME, xs, keep=lambda x: x.amount < 100)] == ["A"]
    assert [p for _, p in recipients(ME, xs)] == ["C"]
