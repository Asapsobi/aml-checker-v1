from datetime import UTC, datetime, timedelta
from decimal import Decimal

from amlcheck.chain.base import History, Transfer
from amlcheck.core.models import Chain
from amlcheck.profile.features import Profile, profile

ME = "TME"
T0 = datetime(2026, 9, 1, tzinfo=UTC)
SINCE, UNTIL = T0 - timedelta(days=90), T0 + timedelta(days=10)


def t(hours: float, other: str, amount: str, *, out: bool = False, n: int = 0) -> Transfer:
    return Transfer(
        Chain.TRON,
        f"tx{hours}-{other}-{n}",
        0,
        None,
        T0 + timedelta(hours=hours),
        ME if out else other,
        other if out else ME,
        Decimal(amount),
    )


def prof(
    xs: list[Transfer],
    *,
    complete: bool = True,
    first: datetime | None = None,
    contract: bool = False,
) -> Profile:
    h = History(tuple(xs), SINCE, UNTIL, complete, first, 0)
    return profile(
        ME, h, is_contract=contract, small_usdt=Decimal(100), round_unit_usdt=Decimal(100)
    )


XS = [
    t(0, "A", "1000"),
    t(1, "B", "50"),
    t(2, "A", "300"),
    t(5, "X", "900", out=True),
    t(30, "Y", "300", out=True),
]


def test_counts_volumes_and_distincts() -> None:
    p = prof(XS)
    assert (p.n_in, p.n_out) == (3, 2)
    assert (p.distinct_senders, p.distinct_recipients, p.counterparties) == (2, 2, 4)
    assert (p.volume_in, p.volume_out) == (Decimal(1350), Decimal(1200))


def test_retained_share() -> None:
    assert prof(XS).retained_share == Decimal(150) / Decimal(1350)
    assert prof([t(0, "A", "10"), t(1, "B", "20", out=True)]).retained_share == 0  # never negative
    assert prof([t(1, "B", "20", out=True)]).retained_share is None


def test_first_and_last_seen() -> None:
    p = prof(XS, first=T0 - timedelta(days=400))
    assert p.first_seen == T0 - timedelta(days=400)
    assert p.last_seen == T0 + timedelta(hours=30)
    assert prof(XS).first_seen == T0


def test_median_hold_hours_amount_weighted() -> None:
    # Spent: 900 of the 0 h lot at 5 h; at 30 h, 100 of the 0 h lot, 50 of 1 h, 150 of 2 h.
    # Holds by amount: 5h×900, 30h×100, 29h×50, 28h×150 → half of 1200 is reached at 5 h.
    assert prof(XS).median_hold_hours == Decimal(5)
    assert prof([t(0, "A", "10")]).median_hold_hours is None


def test_pass_through_share() -> None:
    # Within 24 h: 900 left at 5 h. Of the 300 at 30 h, 100 came at 0 h (30 h ago, not counted),
    # 50 at 1 h (29 h), 150 at 2 h (28 h): none within 24 h → 900 / 1350.
    assert prof(XS).pass_through_share_24h == Decimal(900) / Decimal(1350)


def test_top_recipient_and_sender_shares() -> None:
    p = prof(XS)
    assert p.top_recipient == "X"
    assert p.top_recipient_share_out == Decimal(900) / Decimal(1200)
    assert p.top_sender_share_in == Decimal(1300) / Decimal(1350)
    tie = prof([t(0, "A", "10"), t(1, "Q", "5", out=True), t(2, "P", "5", out=True)])
    assert tie.top_recipient == "P"  # ties broken by address


def test_small_and_round_shares() -> None:
    p = prof(XS)
    assert p.small_in_share == Decimal(1) / Decimal(3)  # only the 50 is under 100
    assert p.round_share == Decimal(4) / Decimal(5)  # 1000, 300, 900, 300 are multiples of 100
    assert prof([t(0, "A", "100")]).small_in_share == 0  # 100 is not under 100


def test_busiest_windows() -> None:
    xs = [t(i, f"S{i}", "5", n=i) for i in range(30)] + [
        t(40 + i, f"R{i}", "1", out=True, n=i) for i in range(3)
    ]
    p = prof(xs)
    assert p.max_senders_24h == 25  # 0 h … 24 h inclusive
    assert p.max_recipients_24h == 3


def test_capped_contract_and_self_transfers() -> None:
    selfie = Transfer(Chain.TRON, "s", 0, None, T0, ME, ME, Decimal(7))
    p = prof([selfie, t(0, "A", "1")], complete=False, contract=True)
    assert (p.capped, p.is_contract, p.n_in, p.n_out) == (True, True, 1, 0)


def test_transfers_outside_the_window_ignored() -> None:
    old = Transfer(Chain.TRON, "old", 0, None, SINCE - timedelta(days=1), "A", ME, Decimal(5))
    assert prof([old]).n_in == 0
