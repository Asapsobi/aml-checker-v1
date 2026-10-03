import json
from datetime import UTC, datetime
from decimal import Decimal

from amlcheck.core.models import Chain
from amlcheck.trace.model import Budget, Edge, Node, NodeClass, Trace, TracePath, dec

T0 = datetime(2026, 10, 1, 10, tzinfo=UTC)


def sample() -> Trace:
    return Trace(
        chain=Chain.TRON,
        target="TT",
        direction="in",
        as_of=T0,
        target_inflow=Decimal(20000),
        nodes=(
            Node(
                "TA",
                1,
                Decimal("0.6"),
                "exchange_regulated",
                False,
                "TT",
                4,
                NodeClass("HUB", Decimal("0.95")),
            ),
            Node("TB", 1, Decimal("0.3"), None, True, "TT"),
        ),
        edges=(Edge("TA", "TT", Decimal(12000), T0, T0, ("tx1",)),),
        partition={
            "exchange_regulated": Decimal("0.6"),
            "untraced:pruned": Decimal("0.1"),
            "sanctioned": Decimal(1) / Decimal(3) - Decimal("0.0333333333"),
        },
        annotations={"layering": Decimal(0)},
        coverage=Decimal("0.9"),
        paths=(TracePath("sanctioned", 2, ("TT", "TB", "TD"), Decimal(4000), Decimal(4000), 2),),
        budget=Budget(4, 11, 2, 9.43),
    )


def test_json_round_trip_with_decimals_as_strings() -> None:
    t = sample()
    js = t.to_json()
    text = json.dumps(js)
    assert (
        '"partition": {"exchange_regulated": "0.6", "sanctioned": "0.3", "untraced:pruned": "0.1"}'
        in text
    )
    assert js["nodes"][0]["classification"] == {"type": "HUB", "confidence": "0.95"}
    assert js["budget"]["seconds"] == 9.4
    back = Trace.from_json(json.loads(text))
    assert back.to_json() == js


def test_dec() -> None:
    assert dec(Decimal("20000")) == "20000"
    assert dec(Decimal(1) / Decimal(3)) == "0.333333"
    assert dec(Decimal("0.10")) == "0.1"
