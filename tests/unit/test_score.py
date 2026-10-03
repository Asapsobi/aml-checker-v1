import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from amlcheck.core.address import detect
from amlcheck.core.models import Finding, Severity, Verdict
from amlcheck.core.score import band, compute, from_json, hazard
from amlcheck.storage.db import open_db
from amlcheck.trace.adapter import summary
from amlcheck.trace.model import Budget, Node, NodeClass, Trace
from tests.unit.trace_world import Fake, T, engine, example, setup_example

D0 = Decimal
NOW = datetime(2026, 10, 3, tzinfo=UTC)


def f(rule: str, n: int = 0) -> Finding:
    return Finding(rule, Severity.REVIEW, "x", f"{rule} #{n}", NOW)


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / "a.db")


# AT-42: the §7.11 example with R-HEU-01 → 66, high; E 59.5, D 0, B 5, U 1.
async def test_at42_worked_example(conn: sqlite3.Connection) -> None:
    eng, store = engine(conn, Fake(example()))
    setup_example(conn, store)
    trace = await eng.run(detect(T))
    assert hazard(trace) == D0("0.24")  # 1.0 × 0.20 + 0.5 × 0.10 × 0.8
    ev = {**summary(trace), "complete": True}
    s = compute(Verdict.REVIEW, [f("R-HEU-01"), f("R-TRC-01")], ev)
    assert (s.score, s.band, s.lower_bound) == (66, "high", False)
    assert (s.exposure, s.direct, s.behaviour, s.uncertainty) == (D0("59.5"), D0(0), D0(5), D0(1))
    assert s.shown == "66 · high"
    assert s.breakdown == "E 59.5 · D 0 · B 5 · U 1"


# AT-43: BLOCK → 100; INCOMPLETE with E = 34 → "≥ 34", band "medium+".
def test_at43_block_and_incomplete() -> None:
    block = compute(Verdict.BLOCK, [f("R-SAN-01")])
    assert (block.score, block.band, block.shown) == (100, "severe", "100 · severe")
    partial = {"hazard": "0.0418", "coverage": None, "complete": False}
    s = compute(Verdict.INCOMPLETE, [f("R-SYS-01")], partial)
    assert s.exposure == D0("34.0")
    assert s.uncertainty == 0  # a partial trace adds no U (D-052)
    assert (s.score, s.lower_bound, s.shown) == (34, True, "≥ 34 · medium+")


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (0, "low"),
        (19, "low"),
        (20, "medium"),
        (49, "medium"),
        (50, "high"),
        (79, "high"),
        (80, "severe"),
        (100, "severe"),
    ],
)
def test_bands(score: int, expected: str) -> None:
    assert band(score) == expected


def test_caps_and_99() -> None:
    every = [f(r) for r in ("R-EXP-01", "R-EXP-02", "R-HEU-02", "R-HEU-05", "R-HEU-06")]
    s = compute(Verdict.REVIEW, every, {"hazard": "1", "coverage": "0", "complete": True})
    assert (s.direct, s.behaviour, s.exposure, s.uncertainty) == (D0(30), D0(30), D0(60), D0(10))
    assert s.score == 99  # only BLOCK reaches 100


def test_a_rule_counts_once() -> None:
    s = compute(Verdict.REVIEW, [f("R-EXP-01", i) for i in range(10)] + [f("R-HEU-01")])
    assert (s.direct, s.behaviour) == (D0(25), D0(5))
    assert s.score == 30


def test_no_trace_no_exposure_or_uncertainty() -> None:
    s = compute(Verdict.NO_HITS, [])
    assert (s.score, s.band, s.hazard) == (0, "low", None)
    assert s.exposure == s.uncertainty == 0
    no_inflow = compute(Verdict.NO_HITS, [], {"hazard": "0", "coverage": None, "complete": True})
    assert no_inflow.uncertainty == 0  # nothing to trace (D-052)
    assert no_inflow.score == 0


# D-052: components to 0.1 half up; the score from the stored components, half up.
@pytest.mark.parametrize(
    ("coverage", "u", "score"),
    [("0.95", "0.5", 1), ("0.955", "0.5", 1), ("0.965", "0.4", 0), ("0.85", "1.5", 2)],
)
def test_rounding_half_up(coverage: str, u: str, score: int) -> None:
    s = compute(Verdict.NO_HITS, [], {"hazard": "0", "coverage": coverage, "complete": True})
    assert s.uncertainty == D0(u)
    assert s.score == score
    # The stored breakdown reproduces the score.
    total = s.exposure + s.direct + s.behaviour + s.uncertainty
    assert s.score == int((total + D0("0.5")) // 1)


def _trace(*nodes: Node, layering: str = "0") -> Trace:
    return Trace(
        chain=detect(T).chain,
        target=T,
        direction="in",
        as_of=NOW,
        target_inflow=D0(100),
        nodes=nodes,
        edges=(),
        partition={},
        annotations={"layering": D0(layering)},
        coverage=None,
        paths=(),
        budget=Budget(0, 0, 0, 0.0),
    )


# D-052: an inferred terminal counts at its own confidence (share-weighted); others at 1.
def test_hazard_inferred_confidence_share_weighted() -> None:
    t = _trace(
        Node(
            "a", 1, D0("0.3"), "suspicious_collector", True, T, 9, NodeClass("COLLECTOR", D0("0.6"))
        ),
        Node(
            "b", 1, D0("0.1"), "suspicious_collector", True, T, 9, NodeClass("COLLECTOR", D0("1"))
        ),
        Node("c", 1, D0("0.2"), "service_unattributed", True, T, 8, NodeClass("HUB", D0("0.95"))),
        Node("d", 1, D0("0.1"), "scam", False, T, 4),
        Node("e", 1, D0("0.3"), "untraced:pruned", False, T, None),
    )
    # 0.5 × (0.3 × 0.6 + 0.1 × 1) + 0.1 × 0.2 × 0.95 + 0.7 × 0.1
    assert hazard(t) == D0("0.5") * (D0("0.18") + D0("0.1")) + D0("0.019") + D0("0.07")


def test_hazard_layering_and_clamp() -> None:
    t = _trace(Node("a", 1, D0("1"), "sanctioned", False, T, 2), layering="0.6")
    assert hazard(t) == 1  # 1.0 + 0.5 × 0.6, clamped to the range 0–1


def test_json_round_trip() -> None:
    s = compute(Verdict.INCOMPLETE, [f("R-HEU-06")], {"hazard": "0.1", "complete": False})
    assert from_json(s.dumps()) == s
    assert s.exposure == D0("51.9")  # 10% sanctioned ≈ 52 (methodology §9 table)
    assert s.dumps() == (
        '{"band":"high","components":{"B":"20","D":"0","E":"51.9","U":"0"},"hazard":"0.1",'
        '"lower_bound":true,"score":72,"score_version":1}'
    )
    assert '"score_version":1' in s.dumps()
