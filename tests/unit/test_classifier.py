from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from amlcheck.config import Classifier
from amlcheck.profile.classifier import ClassifyContext, classify, primary
from amlcheck.profile.features import Profile

NOW = datetime(2026, 10, 3, tzinfo=UTC)
S = Classifier()
D = Decimal


def base(**kw: object) -> Profile:
    p = Profile(
        address="T",
        since=NOW - timedelta(days=90),
        until=NOW,
        n_in=0,
        n_out=0,
        distinct_senders=0,
        distinct_recipients=0,
        volume_in=D(0),
        volume_out=D(0),
        retained_share=None,
        first_seen=NOW - timedelta(days=300),
        last_seen=NOW,
        median_hold_hours=None,
        pass_through_share_24h=None,
        top_recipient=None,
        top_recipient_share_out=None,
        top_sender_share_in=None,
        small_in_share=None,
        round_share=None,
        max_senders_24h=0,
        max_recipients_24h=0,
        capped=False,
        is_contract=False,
    )
    return replace(p, **kw)  # type: ignore[arg-type]


def types(p: Profile, ctx: ClassifyContext | None = None) -> dict[str, Decimal]:
    return {c.type: c.confidence for c in classify(p, ctx or ClassifyContext(), S, NOW)}


HUB_CTX = ClassifyContext(top_recipient_is_hub=True)


def deposit(**kw: object) -> Profile:
    d: dict[str, object] = dict(
        distinct_senders=2,
        distinct_recipients=1,
        n_in=3,
        n_out=1,
        top_recipient="THUB",
        top_recipient_share_out=D("0.9"),
        median_hold_hours=D(72),
        retained_share=D("0.05"),
        volume_in=D(1000),
        volume_out=D(950),
    )
    d.update(kw)
    return base(**d)


def collector(**kw: object) -> Profile:
    d: dict[str, object] = dict(
        distinct_senders=30,
        distinct_recipients=1,
        small_in_share=D("0.7"),
        top_recipient_share_out=D("0.8"),
        n_in=40,
        n_out=2,
    )
    d.update(kw)
    return base(**d)


# AT-32: 600 distinct counterparties → HUB 0.7; capped read → 0.95.
def test_at32_hub() -> None:
    assert types(base(distinct_senders=400, distinct_recipients=200))["HUB"] == D("0.7")
    assert types(base(capped=True))["HUB"] == D("0.95")


# AT-34: 120 senders, 92% of inbound < 100 USDT, 85% forwarded to one address → COLLECTOR ≥ 0.7.
def test_at34_collector() -> None:
    got = types(
        collector(distinct_senders=120, small_in_share=D("0.92"), top_recipient_share_out=D("0.85"))
    )
    assert got["COLLECTOR"] == D("0.8")  # base 0.6 + ≥100 senders + small share ≥ 0.9


# AT-36: every condition fires at its threshold and not just past it.
@pytest.mark.parametrize(
    ("profile", "ctx", "kind", "fires"),
    [
        (base(distinct_senders=300, distinct_recipients=200), None, "HUB", True),
        (base(distinct_senders=300, distinct_recipients=199), None, "HUB", False),
        (deposit(), HUB_CTX, "DEPOSIT", True),
        (deposit(), None, "DEPOSIT", False),  # top recipient not a hub
        (deposit(distinct_senders=1), HUB_CTX, "DEPOSIT", False),
        (deposit(top_recipient_share_out=D("0.8999")), HUB_CTX, "DEPOSIT", False),
        (deposit(median_hold_hours=D("72.01")), HUB_CTX, "DEPOSIT", False),
        (deposit(retained_share=D("0.0501")), HUB_CTX, "DEPOSIT", False),
        (deposit(n_out=4), HUB_CTX, "DEPOSIT", False),  # n_out > n_in
        (deposit(median_hold_hours=None), HUB_CTX, "DEPOSIT", False),  # nothing ever left
        (collector(), None, "COLLECTOR", True),
        (collector(distinct_senders=29), None, "COLLECTOR", False),
        (collector(small_in_share=D("0.6999")), None, "COLLECTOR", False),
        (collector(top_recipient_share_out=D("0.7999")), None, "COLLECTOR", False),
        (
            base(max_recipients_24h=30, distinct_senders=3, distinct_recipients=30),
            None,
            "DISTRIBUTOR",
            True,
        ),
        (
            base(max_recipients_24h=29, distinct_senders=3, distinct_recipients=29),
            None,
            "DISTRIBUTOR",
            False,
        ),
        (
            base(max_recipients_24h=30, distinct_senders=4, distinct_recipients=30),
            None,
            "DISTRIBUTOR",
            False,
        ),
        (base(pass_through_share_24h=D("0.9"), volume_in=D(1000)), None, "PASS_THROUGH", True),
        (base(pass_through_share_24h=D("0.8999"), volume_in=D(1000)), None, "PASS_THROUGH", False),
        (base(pass_through_share_24h=D("0.9"), volume_in=D("999.99")), None, "PASS_THROUGH", False),
        (base(distinct_senders=25, distinct_recipients=25), None, "PERSONAL", True),
        (base(distinct_senders=25, distinct_recipients=26), None, "PERSONAL", False),
        (base(is_contract=True), None, "CONTRACT", True),
        (base(first_seen=NOW - timedelta(days=7) + timedelta(seconds=1)), None, "FRESH", True),
        (base(first_seen=NOW - timedelta(days=7)), None, "FRESH", False),
    ],
)
def test_at36_boundaries(
    profile: Profile, ctx: ClassifyContext | None, kind: str, fires: bool
) -> None:
    assert (kind in types(profile, ctx)) is fires


def test_deposit_bonuses_and_cap() -> None:
    full = deposit(
        distinct_senders=5, median_hold_hours=D(12), top_recipient_share_out=D("0.99"), n_in=6
    )
    assert types(full, ClassifyContext(top_recipient_is_hub=True, hub_entity_named=True))[
        "DEPOSIT"
    ] == D("1.0")
    hub_plus = base(capped=True, distinct_senders=900, distinct_recipients=900)
    assert types(hub_plus)["HUB"] == D("0.95")


def test_deposit_excludes_collector_and_pass_through() -> None:
    p = deposit(
        distinct_senders=40,
        small_in_share=D("0.9"),
        top_recipient_share_out=D("0.95"),
        pass_through_share_24h=D("0.95"),
        volume_in=D(5000),
        n_in=50,
    )
    got = types(p, HUB_CTX)
    assert "DEPOSIT" in got
    assert "COLLECTOR" not in got
    assert "PASS_THROUGH" not in got


def test_primary_is_first_in_table_order_and_fresh_is_a_tag() -> None:
    p = base(is_contract=True, capped=True, first_seen=NOW - timedelta(days=1))
    cs = classify(p, ClassifyContext(), S, NOW)
    assert [c.type for c in cs] == ["CONTRACT", "HUB", "FRESH"]
    first = primary(cs)
    assert first is not None
    assert first.type == "CONTRACT"
    assert not next(c for c in cs if c.type == "FRESH").primary


def test_personal_only_when_nothing_else() -> None:
    assert types(base(distinct_senders=2, distinct_recipients=2)) == {"PERSONAL": D("0.5")}
    assert "PERSONAL" not in types(base(is_contract=True))


def test_distributor_bonuses() -> None:
    p = base(
        max_recipients_24h=100, distinct_senders=1, distinct_recipients=100, round_share=D("0.5")
    )
    assert types(p)["DISTRIBUTOR"] == D("1.0")


def test_conditions_are_recorded() -> None:
    (c,) = [x for x in classify(collector(), ClassifyContext(), S, NOW) if x.type == "COLLECTOR"]
    assert "distinct_senders ≥ 30" in c.conditions
    assert c.bonuses == ()


def test_save_and_cached_with_expiry(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from amlcheck.core.models import Chain
    from amlcheck.profile.classifier import cached, save
    from amlcheck.storage.db import open_db

    conn = open_db(tmp_path / "a.db")
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "classifications" in tables
    p = base(capped=True, first_seen=NOW - timedelta(days=1))
    cs = classify(p, ClassifyContext(), S, NOW)
    save(conn, Chain.TRON, p, cs, S, NOW)
    again = cached(conn, Chain.TRON, "T", NOW + timedelta(days=13))
    assert again is not None
    assert [(c.type, c.confidence, c.primary) for c in again] == [
        (c.type, c.confidence, c.primary) for c in cs
    ]
    assert cached(conn, Chain.TRON, "T", NOW + timedelta(days=14)) is None  # expired (F8.2)
    assert cached(conn, Chain.TRON, "other", NOW) is None
