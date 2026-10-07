"""Read ahead (D-088): several reads in flight per direction; the same trace, sooner."""

import asyncio
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

from amlcheck.chain.base import History, Transfer
from amlcheck.chain.cache import ContractCache, TransferCache
from amlcheck.config import Cache, Settings, Trace
from amlcheck.core.address import detect
from amlcheck.core.clock import fixed
from amlcheck.intel.store import IntelStore
from amlcheck.net.http import SourceError
from amlcheck.storage.db import open_db
from amlcheck.trace.engine import TraceEngine, TraceFailed
from amlcheck.trace.model import Trace as Traced
from tests.unit.test_trace_engine import Slow
from tests.unit.trace_world import NOW, B, Fake, NoContracts, T, addr, example, setup_example, tr


def tree(depth: int = 3, fan: int = 4) -> list[Transfer]:
    """T is paid by `fan` senders, each paid by `fan` more, `depth` hops deep. Shares differ, so
    the order of the queue is fixed."""
    xs: list[Transfer] = []
    level = [(T, "", 1_000_000)]
    for hop in range(1, depth + 1):
        below = []
        for parent, name, amount in level:
            for i in range(fan):
                child = f"{name}{i}"
                share = amount * (fan - i) * 2 // (fan * (fan + 1))
                xs.append(tr(hop * 3 + i * 0.1, addr(f"n{child}"), parent, share))
                below.append((addr(f"n{child}"), child, share))
        level = below
    return xs


@dataclass
class Lagging(Fake):
    """Every read takes a moment, so reads can overlap; it counts how many are in flight. An
    address in `flaky` fails its first read only."""

    flaky: set[str] = field(default_factory=set)
    in_flight: int = 0
    most: int = 0

    async def fetch(
        self,
        address: str,
        since: datetime,
        until: datetime | None,
        limit: int,
        *,
        first_activity: bool,
    ) -> History:
        self.in_flight += 1
        self.most = max(self.most, self.in_flight)
        try:
            await asyncio.sleep(0.002)
            if address in self.flaky:
                self.flaky.discard(address)
                raise SourceError("hypersync", "HTTP 503 after 3 tries")
            return await super().fetch(address, since, until, limit, first_activity=first_activity)
        finally:
            self.in_flight -= 1


def run(conn: sqlite3.Connection, fake: Fake, parallel: int, budget_s: int = 180) -> TraceEngine:
    """The test world is BSC: `bsc_parallel_reads` is the key that applies."""
    return TraceEngine(
        conn,
        TransferCache(conn, {fake.chain: fake}, Cache(), clock=fixed(NOW)),
        ContractCache(conn, {fake.chain: NoContracts()}, clock=fixed(NOW)),
        IntelStore(conn, clock=fixed(NOW)),
        Settings(trace=Trace(bsc_parallel_reads=parallel, time_budget_seconds=budget_s)),
        clock=fixed(NOW),
        monotonic=time.monotonic,
    )


def result(t: Traced) -> tuple[object, ...]:
    """What a trace concludes: everything but its cost."""
    return (t.nodes, t.edges, t.partition, t.coverage, t.paths, t.complete, t.stopped)


async def test_the_same_trace_as_one_at_a_time(tmp_path: Path) -> None:
    single = Lagging(tree())
    one = await run(open_db(tmp_path / "1.db"), single, 1).run(detect(T))
    fake = Lagging(tree())
    four = await run(open_db(tmp_path / "4.db"), fake, 4).run(detect(T))
    assert len(one.nodes) > 20  # a queue deep enough to read ahead
    assert result(four) == result(one)
    assert fake.most > 1  # reads did overlap
    assert sorted(fake.asked) == sorted(single.asked)  # the step uses the answer: no read twice


async def test_one_at_a_time_never_overlaps(tmp_path: Path) -> None:
    fake = Lagging(tree())
    await run(open_db(tmp_path / "a.db"), fake, 1).run(detect(T))
    assert fake.most == 1


async def test_a_failed_read_ahead_is_left_to_the_step(tmp_path: Path) -> None:
    """The step reads again: a passing error costs a read, not the trace. One at a time, the same
    error fails it, as before (non-negotiable 1)."""
    second = addr("n1")  # T's second-largest sender: its first read is a read ahead
    clean = await run(open_db(tmp_path / "c.db"), Lagging(tree()), 1).run(detect(T))
    ahead = await run(open_db(tmp_path / "a.db"), Lagging(tree(), flaky={second}), 4).run(detect(T))
    assert result(ahead) == result(clean)
    with pytest.raises(TraceFailed) as exc:
        await run(open_db(tmp_path / "o.db"), Lagging(tree(), flaky={second}), 1).run(detect(T))
    assert second in exc.value.reason


async def test_out_of_time_reads_ahead_are_cancelled(tmp_path: Path) -> None:
    """D-080 with reads ahead: B's read hangs; the trace ends on time, as one at a time does, and
    leaves no read running."""
    conn = open_db(tmp_path / "a.db")
    setup_example(conn, IntelStore(conn, clock=fixed(NOW)))
    started = time.monotonic()
    t = await run(conn, Slow(example(), slow=B), 4, budget_s=1).run(detect(T))
    assert time.monotonic() - started < 5
    assert t.stopped == "time"
    assert t.partition["untraced:budget"] == Decimal("0.3")  # B, cut off mid-read
    assert asyncio.all_tasks() == {asyncio.current_task()}
