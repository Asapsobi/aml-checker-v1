"""Risk policy v2: exposures (methodology §11, D-071)."""

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from amlcheck.core.address import detect
from amlcheck.core.models import Chain
from amlcheck.core.risk import RISK, Exposure, flag_category, from_trace, ordered, risk_category
from amlcheck.trace.model import Budget, Edge, Node, NodeClass, Trace
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


def _trace(nodes: list[Node], direction: str = "in") -> Trace:
    return Trace(
        chain=Chain.TRON,
        target="T0",
        direction=direction,
        as_of=datetime(2026, 10, 1, tzinfo=UTC),
        target_inflow=Decimal(20_000),
        nodes=(Node("T0", 0, Decimal(1), None, True, None), *nodes),
        edges=(),
        partition={},
        annotations={},
        coverage=Decimal(1),
        paths=(),
        budget=Budget(0, 0, 0, 0.0),
    )


# AT-61: a sanctioned terminal at hop 2 (weight 0.1) and a scam terminal at hop 3 are indirect
# exposures; with decay 0.4 they count × 0.6 and × 0.36, with decay 0 in full.
def test_at61_indirect_exposures_with_decay() -> None:
    trace = _trace(
        [
            Node("TA", 1, Decimal("0.5"), None, True, "T0", 11, path=("T0",)),
            Node("TS", 2, Decimal("0.1"), "sanctioned", False, "TA", 2, path=("T0", "TA")),
            Node("TB", 2, Decimal("0.3"), None, True, "TA", 11, path=("T0", "TA")),
            Node("TC", 3, Decimal("0.05"), "scam", False, "TB", 4, path=("T0", "TA", "TB")),
            Node("TU", 2, Decimal("0.1"), "service_unattributed", True, "TA", 8, path=("T0", "TA")),
        ]
    )
    got = from_trace(trace, lambda a, c: f"name of {a}")
    assert [(e.address, e.hop, e.category, e.exposure_type) for e in got] == [
        ("TS", 2, "sanctioned", "indirect"),
        ("TC", 3, "scam", "indirect"),
    ]  # an unknown service is no exposure (D-071)
    sanctioned, scam = got
    assert sanctioned.volume == Decimal(2000)  # weight × flow, an estimate (D-016)
    assert sanctioned.path == ("T0", "TA", "TS")
    assert sanctioned.entity == "name of TS"
    assert sanctioned.contribution(Decimal("0.4")) == Decimal("0.06")
    assert scam.contribution(Decimal("0.4")) == Decimal("0.7") * Decimal("0.05") * Decimal("0.36")
    assert sanctioned.contribution(Decimal(0)) == Decimal("0.1")


def test_hop1_flagged_is_direct_not_indirect() -> None:
    """Hop-1 terminals from local flags (tests 2–4) are already direct exposures, with exact
    amounts; a hop-1 terminal from a classification (test 9) is not, so the trace adds it."""
    cls = NodeClass("COLLECTOR", Decimal("0.8"))
    trace = _trace(
        [
            Node("TS", 1, Decimal("0.2"), "sanctioned", False, "T0", 2, path=("T0",)),
            Node("TK", 1, Decimal("0.4"), "suspicious_collector", True, "T0", 9, cls, ("T0",)),
        ],
        direction="out",
    )
    (e,) = from_trace(trace, lambda a, c: "unused")
    assert (e.address, e.direction, e.hop, e.inferred) == ("TK", "out", 1, True)
    assert e.entity == "COLLECTOR"  # shown with "(inferred, 0.8)"
    assert e.confidence == Decimal("0.8")
    assert e.contribution(Decimal("0.4")) == Decimal("0.5") * Decimal("0.4") * Decimal("0.8")


def _edge(sender: str, recipient: str, amount: str) -> Edge:
    t = datetime(2026, 9, 1, tzinfo=UTC)
    return Edge(sender, recipient, Decimal(amount), t, t, ())


# D-078: an indirect exposure's volume is its path's bottleneck; paths through one first-hop
# counterparty are capped together at what it sent; the proportional estimate is kept.
def test_path_volume_capped_per_first_hop() -> None:
    trace = replace(
        _trace(
            [
                Node("TA", 1, Decimal("0.3"), None, True, "T0", 11, path=("T0",)),
                Node(
                    "TS1",
                    2,
                    Decimal("0.15"),
                    "sanctioned",
                    False,
                    "TA",
                    2,
                    path=("T0", "TA"),
                    bottleneck=Decimal(2500),
                ),
                Node(
                    "TS2",
                    2,
                    Decimal("0.1"),
                    "sanctioned",
                    False,
                    "TA",
                    2,
                    path=("T0", "TA"),
                    bottleneck=Decimal(2000),
                ),
                Node("TB", 1, Decimal("0.2"), None, True, "T0", 11, path=("T0",)),
                Node(
                    "TM",
                    2,
                    Decimal("0.02"),
                    "mixer",
                    False,
                    "TB",
                    4,
                    path=("T0", "TB"),
                    bottleneck=Decimal(500),
                ),
            ]
        ),
        target_inflow=Decimal(10_000),
        edges=(_edge("TA", "T0", "3000"), _edge("TB", "T0", "2000"), _edge("TS1", "TA", "2500")),
    )
    by = {e.address: e for e in from_trace(trace, lambda a, c: a)}
    # 2,500 + 2,000 through TA, which sent only 3,000: scaled by 3,000 / 4,500.
    assert by["TS1"].volume == Decimal(2500) * 3000 / 4500
    assert by["TS2"].volume == Decimal(2000) * 3000 / 4500
    assert by["TS1"].volume + by["TS2"].volume == Decimal(3000)
    assert by["TS1"].percent == by["TS1"].volume / 10_000
    assert by["TM"].volume == Decimal(500)  # under TB's 2,000: kept
    assert by["TM"].percent == Decimal("0.05")
    assert (by["TS1"].estimate, by["TM"].estimate) == (Decimal(1500), Decimal(200))
    prop = {e.address: e for e in from_trace(trace, lambda a, c: a, "proportional")}
    assert (prop["TS1"].percent, prop["TM"].percent) == (Decimal("0.15"), Decimal("0.02"))
    assert Exposure.from_json(by["TS1"].to_json()).estimate == Decimal(1500)


def test_old_traces_without_bottlenecks_fall_back_to_the_estimate() -> None:
    trace = _trace([Node("TS", 2, Decimal("0.1"), "sanctioned", False, "TA", 2, path=("T0", "TA"))])
    (e,) = from_trace(trace, lambda a, c: a)
    assert e.volume == e.estimate == Decimal(2000)
