"""Freeze neighbours: suspected_malicious, inferred in a trace (methodology §13.2, AT-72)."""

import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from amlcheck.core.address import detect
from amlcheck.core.models import Chain
from amlcheck.core.risk import from_trace
from amlcheck.storage.db import open_db
from tests.unit.trace_world import A, B, C, Fake, T, addr, engine, sanction, tr

D0 = Decimal
BAD = addr("bad")


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / "a.db")


def world(sent_to_bad: int) -> list:  # type: ignore[type-arg]
    """T received 7,000 from A. A, in its window, sent 7,000 to T and `sent_to_bad` to BAD, and
    was paid by B, who was paid by C."""
    return [
        tr(5, A, T, 7000),
        tr(10, A, BAD, sent_to_bad),
        tr(12, B, A, 10000),
        tr(20, C, B, 10000),
    ]


# AT-72: A sent 30% of what it sent (3,000 USDT) to a sanctioned address: a suspected neighbour at
# hop 1, confidence 0.3, inferred, never a fact. The trace goes on through it (§13.2).
async def test_at72_neighbour_is_suspected_and_traced_through(conn: sqlite3.Connection) -> None:
    sanction(conn, BAD)
    eng, store = engine(conn, Fake(world(3000)))
    t = await eng.run(detect(T))
    a = next(n for n in t.nodes if n.address == A)
    assert (a.terminal, a.test) == (None, 11)  # expanded, not an end
    assert a.classification is not None
    assert (a.classification.type, a.classification.confidence) == (
        "SUSPECTED_MALICIOUS",
        D0("0.3"),
    )
    assert B in {n.address for n in t.nodes}  # read beyond A
    (e,) = from_trace(t, lambda x, c: x)
    assert (e.address, e.hop, e.category, e.inferred, e.confidence) == (
        A,
        1,
        "suspected_malicious",
        True,
        D0("0.3"),
    )
    assert e.volume == D0(7000)  # the path to A: A paid T 7,000
    assert e.risk_type == "illicit_activity"
    assert store.labels(Chain.BSC, A) == []  # an inference about the trace, not a stored label


async def test_below_the_share_is_no_suspicion(conn: sqlite3.Connection) -> None:
    sanction(conn, BAD)
    eng, _ = engine(conn, Fake(world(700)))  # 700 of 7,700 sent: 9%
    t = await eng.run(detect(T))
    a = next(n for n in t.nodes if n.address == A)
    assert a.classification is None
    assert from_trace(t, lambda x, c: x) == []


async def test_facts_behind_a_neighbour_are_still_found(conn: sqlite3.Connection) -> None:
    """A is a neighbour (it sent to BAD) and B, who paid A, is sanctioned: both are exposures,
    capped together at A's own edge to T."""
    sanction(conn, BAD, B)
    eng, _ = engine(conn, Fake(world(3000)))
    t = await eng.run(detect(T))
    assert t.partition == {"sanctioned": D0(1)}
    found = {(e.address, e.category, e.hop) for e in from_trace(t, lambda x, c: x)}
    assert found == {(A, "suspected_malicious", 1), (B, "sanctioned", 2)}
    assert sum(e.volume for e in from_trace(t, lambda x, c: x)) <= D0(7000)


async def test_flagged_senders_are_followed_as_facts(conn: sqlite3.Connection) -> None:
    """Tracing in, A's sanctioned *sender* is reached by the trace itself: no suspicion."""
    xs = [tr(5, A, T, 1000), tr(10, BAD, A, 1000)]
    sanction(conn, BAD)
    eng, _ = engine(conn, Fake(xs))
    t = await eng.run(detect(T))
    assert t.partition == {"sanctioned": D0(1)}
    a = next(n for n in t.nodes if n.address == A)
    assert a.classification is None
