import asyncio
import json
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from amlcheck.chain.base import History
from amlcheck.chain.cache import ContractCache, TransferCache
from amlcheck.config import Cache
from amlcheck.core.address import detect
from amlcheck.core.clock import fixed
from amlcheck.intel.store import IntelStore
from amlcheck.storage.db import open_db
from amlcheck.trace.engine import TraceEngine, TraceFailed, _Flow, prune
from tests.unit.trace_world import (
    NOW,
    A,
    B,
    C,
    D,
    E,
    Fake,
    NoContracts,
    T,
    addr,
    engine,
    example,
    example_settings,
    sanction,
    setup_example,
    tr,
)

D0 = Decimal


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / "a.db")


# AT-37: the methodology §7.11 worked example.
async def test_at37_worked_example(conn: sqlite3.Connection) -> None:
    fake = Fake(example())
    eng, store = engine(conn, fake)
    setup_example(conn, store)
    t = await eng.run(detect(T))
    assert t.complete
    assert t.target_inflow == D0(20000)
    assert t.partition == {
        "exchange_regulated": D0("0.6"),
        "sanctioned": D0("0.2"),
        "suspicious_collector": D0("0.1"),
        "untraced:pruned": D0("0.1"),
    }
    assert t.coverage == D0("0.9")
    assert t.budget.nodes_read == 3  # T, B, E (D-047)
    by = {(n.address, n.hop): n for n in t.nodes}
    assert (by[(A, 1)].terminal, by[(A, 1)].test, by[(A, 1)].read) == (
        "exchange_regulated",
        4,
        False,
    )
    assert (by[(D, 2)].terminal, by[(D, 2)].test) == ("sanctioned", 2)
    e = by[(E, 2)]
    assert (e.terminal, e.test, e.read) == ("suspicious_collector", 9, True)
    assert e.classification is not None
    assert (e.classification.type, e.classification.confidence) == ("COLLECTOR", D0("0.8"))
    sanctioned = next(p for p in t.paths if p.to_category == "sanctioned")
    assert sanctioned.addresses == (T, B, D)
    assert (sanctioned.bottleneck, sanctioned.estimated, sanctioned.hops) == (D0(4000), D0(4000), 2)


# AT-39: a node's history read fails → the trace fails with the partial result, naming the node.
async def test_at39_read_failure_keeps_partial(conn: sqlite3.Connection) -> None:
    fake = Fake(example(), fail_on={E})
    eng, store = engine(conn, fake)
    setup_example(conn, store)
    with pytest.raises(TraceFailed) as exc:
        await eng.run(detect(T))
    partial = exc.value.partial
    assert not partial.complete
    assert E in exc.value.reason
    assert "hop 2" in exc.value.reason
    assert partial.partition["sanctioned"] == D0("0.2")  # what was decided before the failure
    assert partial.partition["untraced:unfinished"] == D0("0.1")


# AT-40: bsc_max_nodes = 2 on the example (a BSC world) → remaining weight in untraced:budget;
# not a failure.
async def test_at40_node_budget(conn: sqlite3.Connection) -> None:
    fake = Fake(example())
    eng, store = engine(conn, fake, example_settings(bsc_max_nodes=2))
    setup_example(conn, store)
    t = await eng.run(detect(T))
    assert t.complete
    assert t.partition["untraced:budget"] == D0("0.1")
    assert t.budget.nodes_read == 2
    assert E not in fake.asked


@dataclass
class Slow(Fake):
    """A provider that hangs on one address (a lossy line: bytes trickle, no timeout fires)."""

    slow: str = ""

    async def fetch(
        self,
        address: str,
        since: datetime,
        until: datetime | None,
        limit: int,
        *,
        first_activity: bool,
    ) -> History:
        if address == self.slow:
            await asyncio.sleep(30)
        return await super().fetch(address, since, until, limit, first_activity=first_activity)


def real_time_engine(conn: sqlite3.Connection, fake: Fake, budget_s: int) -> TraceEngine:
    return TraceEngine(
        conn,
        TransferCache(conn, {fake.chain: fake}, Cache(), clock=fixed(NOW)),
        ContractCache(conn, {fake.chain: NoContracts()}, clock=fixed(NOW)),
        IntelStore(conn, clock=fixed(NOW)),
        example_settings(time_budget_seconds=budget_s),
        clock=fixed(NOW),
        monotonic=time.monotonic,
    )


# F9.3, D-080: the time budget also bounds a read in progress; running out ends the trace on time
# with what wasn't finished in untraced:budget, not as a failure.
async def test_time_budget_bounds_a_slow_read(conn: sqlite3.Connection) -> None:
    fake = Slow(example(), slow=B)
    setup_example(conn, IntelStore(conn, clock=fixed(NOW)))
    started = time.monotonic()
    t = await real_time_engine(conn, fake, 1).run(detect(T))
    assert time.monotonic() - started < 5
    assert t.complete
    assert t.stopped == "time"
    assert t.partition["exchange_regulated"] == D0("0.6")  # A, the larger, went first
    assert t.partition["untraced:budget"] == D0("0.3")  # B, cut off mid-read
    assert t.partition["untraced:pruned"] == D0("0.1")  # C, as in §7.11
    assert sum(t.partition.values()) == 1
    assert t.coverage == D0("0.6")


async def test_out_of_time_queue_still_gets_the_local_tests(conn: sqlite3.Connection) -> None:
    """D-080: a sanctioned address still waiting when time runs out is found without a read."""
    xs = [tr(5, A, T, 1000), tr(6, B, T, 900), tr(20, C, B, 900)]
    sanction(conn, B)
    t = await real_time_engine(conn, Slow(xs, slow=A), 1).run(detect(T))
    assert t.stopped == "time"
    assert t.partition["sanctioned"] == D0(900) / D0(1900)  # B: queued behind A, still checked
    assert t.partition["untraced:budget"] == D0(1000) / D0(1900)


async def test_time_budget_bounds_the_target_read(conn: sqlite3.Connection) -> None:
    with pytest.raises(TraceFailed) as exc:
        await real_time_engine(conn, Slow(example(), slow=T), 1).run(detect(T))
    assert "time budget" in exc.value.reason
    assert exc.value.partial.partition == {}


# AT-41: a cycle A → B → A sends its weight to untraced:cycle and terminates.
async def test_at41_cycle(conn: sqlite3.Connection) -> None:
    xs = [tr(5, A, T, 1000), tr(20, B, A, 1000), tr(40, A, B, 1000)]
    eng, _ = engine(conn, Fake(xs))
    t = await eng.run(detect(T))
    assert t.complete
    assert t.partition == {"untraced:cycle": D0(1)}
    hops = [(n.address, n.hop, n.terminal) for n in t.nodes]
    assert (A, 3, "untraced:cycle") in hops


async def test_no_inflow_target(conn: sqlite3.Connection) -> None:
    eng, _ = engine(conn, Fake([tr(5, T, A, 100)]))
    t = await eng.run(detect(T))
    assert t.complete
    assert t.coverage is None
    assert t.partition == {}


async def test_depth_and_no_inflow_buckets(conn: sqlite3.Connection) -> None:
    x, y, z, w, v = addr("x"), addr("y"), addr("z"), addr("w"), addr("v")
    xs = [
        tr(5, A, T, 1000),
        tr(10, x, A, 1000),
        tr(20, y, x, 1000),
        tr(30, z, y, 1000),
        tr(40, w, z, 1000),  # w at hop 5 = max_hops (§12.2): untraced:depth
        tr(50, v, w, 1000),
        tr(6, C, T, 1000),
    ]
    eng, _ = engine(conn, Fake(xs))
    t = await eng.run(detect(T))
    assert t.partition == {"untraced:depth": D0("0.5"), "untraced:no_inflow": D0("0.5")}
    assert sum(t.partition.values()) == 1


async def test_forward_direction_mirrors(conn: sqlite3.Connection) -> None:
    xs = [tr(5, T, A, 1000), tr(4, A, D, 1000)]
    eng, _ = engine(conn, Fake(xs))
    sanction(conn, D)
    t = await eng.run(detect(T), "out")
    # T paid A; A paid D: D sanctioned is where the money went.
    assert t.partition == {"sanctioned": D0(1)}


def test_prune_worked_example() -> None:
    def f(a: int) -> _Flow:
        return _Flow(D0(a))

    kept, pruned = prune(
        {"A": f(12000), "B": f(6000), "C": f(2000)},
        parent_weight=D0(1),
        parent_flow=D0(20000),
        parent_bottleneck=D0(-1),
        branch=5,
        coverage_share=D0("0.8"),
        min_attributed=D0(100),
    )
    assert [k[0] for k in kept] == ["A", "B"]
    assert pruned == D0("0.1")


def test_prune_branch_min_amount_and_ties() -> None:
    def f(a: int) -> _Flow:
        return _Flow(D0(a))

    senders = {f"S{i}": f(10) for i in range(8)}
    kept, pruned = prune(
        senders,
        parent_weight=D0(1),
        parent_flow=D0(80),
        parent_bottleneck=D0(-1),
        branch=5,
        coverage_share=D0(1),
        min_attributed=D0(0),
    )
    assert [k[0] for k in kept] == ["S0", "S1", "S2", "S3", "S4"]
    assert pruned == D0("0.375")
    kept, _ = prune(
        {"big": f(1000), "small": f(50)},
        parent_weight=D0("0.1"),
        parent_flow=D0(1050),
        parent_bottleneck=D0(5000),
        branch=5,
        coverage_share=D0(1),
        min_attributed=D0(100),
    )
    assert [k[0] for k in kept] == ["big"]  # small's path volume 50 < 100


def test_prune_on_path_volume_not_the_estimate() -> None:
    """D-081: 34,733 USDT through a busy middle address is followed although its proportional
    share is tiny (the D-078 case); v1 pruned it on the estimate."""
    kept, _ = prune(
        {"xinbi": _Flow(D0(34733)), "rest": _Flow(D0(965267))},
        parent_weight=D0("0.0001"),
        parent_flow=D0(1_000_000),
        parent_bottleneck=D0(34733),
        branch=5,
        coverage_share=D0(1),
        min_attributed=D0(100),
    )
    assert "xinbi" in [k[0] for k in kept]  # estimate 0.0001 × 3.5% × flow, far below 100


async def test_child_window_is_30_days_before_first_payment(conn: sqlite3.Connection) -> None:
    xs = [tr(10, A, T, 1000), tr(39, B, A, 500), tr(41, C, A, 500)]  # C paid A 41 days before
    eng, _ = engine(conn, Fake(xs))
    t = await eng.run(detect(T))
    seen = {n.address for n in t.nodes}
    assert B in seen
    assert C not in seen  # outside A's window [40 days ago, 10 days ago]


async def test_deterministic(conn: sqlite3.Connection, tmp_path: Path) -> None:
    fake = Fake(example())
    eng, store = engine(conn, fake)
    setup_example(conn, store)
    first = json.dumps((await eng.run(detect(T))).to_json(), sort_keys=True)
    conn2 = open_db(tmp_path / "b.db")
    eng2, store2 = engine(conn2, Fake(list(reversed(example()))))
    setup_example(conn2, store2)
    second = json.dumps((await eng2.run(detect(T))).to_json(), sort_keys=True)
    assert first.replace('"queries": 3', "") == second.replace('"queries": 3', "")
    assert NOW
