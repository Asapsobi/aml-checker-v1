"""Risk policy v2: exposures (methodology §11, D-071)."""

import sqlite3
from decimal import Decimal

from amlcheck.core.address import detect
from amlcheck.core.risk import RISK, Exposure, flag_category, ordered, risk_category
from tests.unit.test_exposure import ME, conn, source, tr

__all__ = ["conn"]  # the fixture, shared with test_exposure


# AT-60: a sanctioned sender (5% of inflow) and a frozen recipient (2% of outflow), both smaller
# than the 20 counterparties shown, are direct exposures with exact amounts and per-direction
# shares.
async def test_at60_direct_exposures_both_ways(conn: sqlite3.Connection) -> None:
    history = [tr(10, "TIN", "9500"), tr(5, "TSANCTIONED", "500")]  # 500 of 10,000 received
    history += [tr(4, f"TOUT{i:02}", "588", out=True) for i in range(20)]  # 11,760 out
    history += [tr(3, "TFROZEN", "240", out=True)]  # 240 of 12,000 sent
    r = await source(conn, history).check(detect(ME))
    shown = {c["address"] for c in r.evidence["counterparties"]}
    assert not {"TSANCTIONED", "TFROZEN"} & shown  # beyond the 20 shown
    got = [Exposure.from_json(e) for e in r.evidence["exposures"]]
    assert [(e.direction, e.address, e.category, e.hop) for e in got] == [
        ("in", "TSANCTIONED", "sanctioned", 1),
        ("out", "TFROZEN", "frozen", 1),
    ]
    sanctioned, frozen = got
    assert (sanctioned.volume, sanctioned.percent) == (Decimal(500), Decimal("0.05"))
    assert (frozen.volume, frozen.percent) == (Decimal(240), Decimal("0.02"))
    assert sanctioned.entity == "OFAC SDN: Bad Co"
    assert frozen.entity == "Tether-frozen"
    assert (sanctioned.exposure_type, sanctioned.risk_type) == ("direct", "sanctioned_entity")
    assert sanctioned.path == (ME, "TSANCTIONED")
    assert r.evidence["exposures"][0]["volume_usdt"] == "500"


async def test_both_directions_with_one_counterparty(conn: sqlite3.Connection) -> None:
    r = await source(
        conn, [tr(5, "TSANCTIONED", "300"), tr(4, "TSANCTIONED", "100", out=True)]
    ).check(detect(ME))
    got = [(e["direction"], e["percent"]) for e in r.evidence["exposures"]]
    assert got == [("in", "1"), ("out", "1")]


async def test_no_risk_no_exposure(conn: sqlite3.Connection) -> None:
    r = await source(conn, [tr(5, "TOK", "300")]).check(detect(ME))
    assert r.evidence["exposures"] == []


def test_flag_categories() -> None:
    assert flag_category("sanctioned") == "sanctioned"
    assert flag_category("label:mixer") == "mixer"
    assert flag_category("label:allowlist") == "own_or_trusted"
    assert flag_category("label:scam") == "scam"
    assert flag_category("label:my friend") is None
    # The most severe risk wins; an allowlist tag never hides a sanctions flag (F5.4).
    assert risk_category({"label:allowlist", "sanctioned", "label:mixer"}) == "sanctioned"
    assert risk_category({"label:allowlist"}) is None  # not a risk
    assert risk_category({"label:exchange_regulated"}) is None


def test_unknown_services_are_not_a_risk() -> None:
    for category in ("service_unattributed", "contract_unattributed", "otc_desk"):
        assert category not in RISK  # D-071: v1 counted them at 0.1


def test_contribution_decay_and_confidence() -> None:
    e = Exposure("in", 1, "TX", "sanctioned", "x", Decimal(10), Decimal("0.1"))
    assert e.contribution(Decimal("0.4")) == Decimal("0.1")
    hop3 = Exposure("in", 3, "TX", "scam", "x", Decimal(10), Decimal("0.1"))
    assert hop3.contribution(Decimal("0.4")) == Decimal("0.7") * Decimal("0.36") * Decimal("0.1")
    assert hop3.contribution(Decimal(0)) == Decimal("0.07")  # decay off
    inferred = Exposure(
        "in", 2, "TX", "suspicious_collector", "x", Decimal(10), Decimal("0.2"), (), Decimal("0.8")
    )
    assert inferred.inferred
    assert inferred.contribution(Decimal(0)) == Decimal("0.08")


def test_json_round_trip_and_order() -> None:
    a = Exposure("out", 2, "TA", "mixer", "m", Decimal("12.5"), Decimal("0.3"), ("T", "X", "TA"))
    b = Exposure("in", 1, "TB", "frozen", "f", Decimal(7), Decimal("0.01"), ("T", "TB"))
    c = Exposure("in", 1, "TC", "sanctioned", "s", Decimal(9), Decimal("0.02"), ("T", "TC"))
    assert Exposure.from_json(a.to_json()) == a
    assert ordered([a, b, c]) == [c, b, a]  # in before out; heavier first
