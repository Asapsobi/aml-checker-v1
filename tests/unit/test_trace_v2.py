"""Trace version 2 (methodology §12.2): both directions, 5 hops, best first (AT-67)."""

import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from amlcheck.chain.base import Transfer
from amlcheck.cli import runtime
from amlcheck.config import Settings, Trace
from amlcheck.core.address import detect
from amlcheck.core.clock import fixed
from amlcheck.core.engine import screen
from amlcheck.core.risk import Exposure
from amlcheck.storage.db import open_db
from amlcheck.trace.adapter import TraceSource
from amlcheck.trace.jobs import TraceJobs
from tests.unit.trace_world import NOW, Fake, T, addr, engine, sanction, tr

D0 = Decimal


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / "a.db")


def deep_world() -> list[Transfer]:
    """Sanctioned 5 hops back on the way in, 4 hops on the way out."""
    a, b, c, d, s = (addr(x) for x in ("in1", "in2", "in3", "in4", "inS"))
    x, y, z, f = (addr(n) for n in ("out1", "out2", "out3", "outF"))
    return [
        tr(5, a, T, 1000),
        tr(10, b, a, 1000),
        tr(15, c, b, 1000),
        tr(20, d, c, 1000),
        tr(25, s, d, 1000),  # hop 5 in: sanctioned
        tr(25, T, x, 1000),
        tr(20, x, y, 1000),
        tr(15, y, z, 1000),
        tr(10, z, f, 1000),  # hop 4 out: sanctioned
    ]


S_IN, F_OUT = addr("inS"), addr("outF")


# AT-67: risk 5 hops back on the way in and 4 hops on the way out are both found in one check;
# each direction's partition sums to 1. (The fake world is BSC: 5 hops set here, D-082.)
async def test_at67_both_directions_deep(conn: sqlite3.Connection) -> None:
    s, f = S_IN, F_OUT
    settings = Settings(trace=Trace(bsc_max_hops=5))
    eng, _ = engine(conn, Fake(deep_world()), settings)
    sanction(conn, s, f)
    src = TraceSource(TraceJobs(conn, settings, clock=fixed(NOW)), eng, settings)
    result = await screen(detect(T), [src], conn=conn, settings=settings, now=fixed(NOW))
    (r,) = result.sources
    found = {
        (e.direction, e.hop, e.address) for e in map(Exposure.from_json, r.evidence["exposures"])
    }
    assert found == {("in", 5, s), ("out", 4, f)}
    assert sum(D0(v) for v in r.evidence["partition"].values()) == 1
    assert sum(D0(v) for v in r.evidence["out"]["partition"].values()) == 1
    assert r.evidence["out"]["trace_id"] != r.evidence["trace_id"]
    assert "in: coverage 100.0%; out: coverage 100.0%" in (r.detail or "")
    assert result.score is not None
    # in: 1.0 × 0.6^4 = 0.1296; out: 1.0 × 0.6^3 = 0.216; H = 1 − 0.8704 × 0.784 = 0.3176
    assert result.score.score >= 91  # severe, though no address the check dealt with is listed


# D-082: BSC stops at 3 hops by default; TRON keeps 5.
async def test_bsc_stops_at_3_hops(conn: sqlite3.Connection) -> None:
    sanction(conn, S_IN, F_OUT)
    eng, _ = engine(conn, Fake(deep_world()))
    t_in = await eng.run(detect(T), "in")
    t_out = await eng.run(detect(T), "out")
    assert t_in.partition == {"untraced:depth": D0(1)}  # stopped at hop 3, short of hop 5
    assert t_out.partition == {"untraced:depth": D0(1)}  # and short of hop 4
    assert max(n.hop for n in t_in.nodes) == 3
    assert Settings().trace.max_hops == 5


# Best first (D-081): a heavy hop-2 branch is read before a light hop-1 one.
async def test_best_first_spends_the_budget_on_the_heaviest(conn: sqlite3.Connection) -> None:
    a, a2, a3, light = (addr(n) for n in ("A", "A2", "A3", "L"))
    xs = [
        tr(5, a, T, 10000),
        tr(6, light, T, 3000),  # kept: A alone covers 77% < 80%
        tr(10, a2, a, 9000),
        tr(15, a3, a2, 9000),
        tr(8, addr("Lsrc"), light, 3000),
    ]
    sanction(conn, a3)
    eng, _ = engine(conn, Fake(xs), Settings(trace=Trace(max_nodes=3)))
    t = await eng.run(detect(T))
    # Reads: T, A, then A2 (9,000 × 0.6 = 5,400) before L (3,000): hop by hop would read L first.
    # A3 is found locally; L is out of the node budget.
    assert t.partition["sanctioned"] == D0(10000) / D0(13000)
    assert t.partition["untraced:budget"] == D0(3000) / D0(13000)
    assert sum(t.partition.values()) == 1


@pytest.mark.parametrize(
    ("every", "asked", "amount", "expected"),
    [
        (True, None, None, True),
        (True, False, D0(50000), False),  # --no-trace still skips it
        (False, None, D0(9999), False),
        (False, None, D0(10000), True),
        (False, True, None, True),
    ],
)
def test_should_trace(
    every: bool, asked: bool | None, amount: Decimal | None, expected: bool
) -> None:
    class Rt:
        settings = Settings(trace=Trace(every_check=every))

    assert runtime.should_trace(Rt(), asked, amount) is expected  # type: ignore[arg-type]
