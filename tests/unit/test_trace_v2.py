"""Trace version 2 (methodology §12.2): both directions, 5 hops, best first (AT-67)."""

import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from amlcheck.chain.base import Transfer
from amlcheck.cli import runtime
from amlcheck.config import Settings, Trace
from amlcheck.core.address import detect
from amlcheck.core.clock import fixed
from amlcheck.core.engine import screen
from amlcheck.core.risk import BEHIND, Exposure, from_trace
from amlcheck.storage.db import open_db
from amlcheck.trace.adapter import TraceSource
from amlcheck.trace.jobs import TraceJobs
from amlcheck.trace.model import Trace as Trace_
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
    eng, _ = engine(conn, Fake(xs), Settings(trace=Trace(bsc_max_nodes=3)))
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


# D-096: the 100 USDT floor scales down for a small wallet, to 1% of its own flow. Coverage share 1
# keeps every sender in play, so only the floor decides.
async def test_a_small_wallets_trace_follows_small_amounts(conn: sqlite3.Connection) -> None:
    a, b, c = addr("sa"), addr("sb"), addr("sc")
    xs = [tr(5, a, T, "8.39"), tr(6, b, T, "4.91"), tr(20, c, a, "8")]
    sanction(conn, c)
    eng, _ = engine(conn, Fake(xs), Settings(trace=Trace(coverage_share=D0(1))))
    t = await eng.run(detect(T))
    assert "untraced:pruned" not in t.partition  # the old floor pruned all 13.30 USDT
    assert abs(t.partition["sanctioned"] - D0("8.39") / D0("13.30")) < D0("1e-12")  # C, behind A


async def test_a_big_wallet_keeps_the_100_usdt_floor(conn: sqlite3.Connection) -> None:
    a, b, c = addr("ba"), addr("bb"), addr("bc")
    xs = [tr(5, a, T, 1_000_000), tr(6, b, T, 50), tr(20, c, b, 50)]
    sanction(conn, c)
    eng, _ = engine(conn, Fake(xs), Settings(trace=Trace(coverage_share=D0(1))))
    t = await eng.run(detect(T))
    assert "sanctioned" not in t.partition  # B's 50 USDT is under min(100, 1% of 1,000,050)
    assert t.partition["untraced:pruned"] == D0(50) / D0(1_000_050)


def busy_service_world() -> tuple[list[Transfer], str, str]:
    """T got 1,000 USDT from a busy service. Its window holds 5 transfers, so with
    `hub_transfers` = 3 it is a hub; the newest 3 hold 100 from a sanctioned address and 300 from
    an ordinary one: 25% of what the sample shows came in from sanctioned money."""
    hub, bad = addr("hub"), addr("hbad")
    xs = [tr(5, hub, T, 1000), tr(6, bad, hub, 100)]
    xs += [tr(6 + n / 10, addr(f"ok{n}"), hub, 300, n) for n in range(1, 4)]
    return xs, hub, bad


# D-097: a busy service the trace stops at passes on what sits behind it, inferred, one hop on.
async def test_what_sits_behind_a_busy_service(conn: sqlite3.Connection) -> None:
    xs, hub, bad = busy_service_world()
    sanction(conn, bad)
    eng, _ = engine(conn, Fake(xs), Settings(trace=Trace(hub_transfers=3)))
    t = await eng.run(detect(T))
    (node,) = [n for n in t.nodes if n.address == hub]
    assert (node.terminal, node.test, node.behind) == (
        "service_unattributed",
        8,
        (("sanctioned", D0("0.25")),),
    )
    (e,) = from_trace(t, lambda x, c: x)
    assert (e.hop, e.address, e.category, e.entity) == (2, hub, "sanctioned", BEHIND)
    assert (e.volume, e.percent, e.confidence, e.inferred) == (D0(250), D0("0.25"), D0("0.5"), True)
    assert e.risk_type == "sanctioned_entity"
    assert t.partition == {"service_unattributed": D0(1)}  # the partition doesn't change
    assert Trace_.from_json(t.to_json()).nodes == t.nodes  # `behind` is kept


async def test_the_pass_through_can_be_turned_off(conn: sqlite3.Connection) -> None:
    xs, hub, bad = busy_service_world()
    sanction(conn, bad)
    off = Settings(trace=Trace(hub_transfers=3, service_pass_through=D0(0)))
    eng, _ = engine(conn, Fake(xs), off)
    t = await eng.run(detect(T))
    assert [n.behind for n in t.nodes if n.address == hub] == [()]
    assert from_trace(t, lambda x, c: x) == []
    assert "behind" not in str(t.to_json())  # a trace without it reads and hashes as before


# D-098: hop 1 is followed wider. The owner's MVP wallet had five big senders filling the five
# slots, and the sixth one passed on money from a frozen address.
async def test_the_first_hop_is_followed_wider(conn: sqlite3.Connection) -> None:
    big = [addr(f"w{n}") for n in range(5)]
    sixth, frozen = addr("w6"), addr("wfrz")
    xs = [tr(5 + n / 10, a, T, 3000 - 200 * n, n) for n, a in enumerate(big)]
    xs += [tr(6, sixth, T, 868), tr(20, frozen, sixth, 868)]
    sanction(conn, frozen)
    narrow, _ = engine(conn, Fake(xs))  # one rule at every hop: five senders at most
    assert "sanctioned" not in (await narrow.run(detect(T))).partition
    wide, _ = engine(conn, Fake(xs), Settings())
    t = await wide.run(detect(T))
    assert t.partition["sanctioned"] > 0  # the sixth sender, then what paid it
    assert sixth in {n.address for n in t.nodes if n.hop == 1}


class Sided(Fake):
    """A provider that can also read one side of an address's transfers (TronGrid, D-099)."""

    async def fetch_side(
        self, address: str, since: datetime, until: datetime, limit: int, side: str
    ) -> list[Transfer]:
        mine = [
            t
            for t in self.transfers
            if (t.recipient if side == "in" else t.sender) == address and since <= t.time <= until
        ]
        return sorted(mine, key=lambda t: t.time, reverse=True)[:limit]


# D-099: a busy wallet's newest transfers can be hours of payouts only; its deposits from a frozen
# wallet a day earlier are found by reading its incoming side.
async def test_behind_a_busy_wallet_its_deposits_are_read(conn: sqlite3.Connection) -> None:
    hub, frozen = addr("xhub"), addr("xfrz")
    xs = [tr(5, hub, T, 868)]
    xs += [tr(5.1 + n / 100, hub, addr(f"payout{n}"), 50, n) for n in range(5)]  # newest: payouts
    xs += [tr(6, frozen, hub, 8000), tr(6.5, addr("xok"), hub, 8000)]  # deposits a day before
    sanction(conn, frozen)
    settings = Settings(trace=Trace(hub_transfers=3))
    sample_only, _ = engine(conn, Fake(xs), settings)
    t = await sample_only.run(detect(T))
    assert [n.behind for n in t.nodes if n.address == hub] == [()]  # the sample had payouts only
    sided, _ = engine(conn, Sided(xs), settings)
    t = await sided.run(detect(T))
    assert [n.behind for n in t.nodes if n.address == hub] == [(("sanctioned", D0("0.5")),)]
