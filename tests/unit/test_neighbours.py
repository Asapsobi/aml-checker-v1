"""Freeze neighbours: suspected_malicious, inferred in a trace (methodology §13.2, AT-72)."""

import sqlite3
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from amlcheck.core.address import detect
from amlcheck.core.clock import to_db
from amlcheck.core.models import Chain
from amlcheck.core.risk import from_trace
from amlcheck.intel.store import IntelStore
from amlcheck.storage.db import open_db
from tests.unit.trace_world import NOW, A, B, C, Fake, T, addr, engine, sanction, tr

D0 = Decimal
BAD = addr("bad")


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / "a.db")


def world(share_out_to_bad: int) -> list:  # type: ignore[type-arg]
    """T received 7,000 from A. A, in its window, sent 7,000 to T and `share_out_to_bad` to BAD,
    and was paid by B."""
    return [
        tr(5, A, T, 7000),
        tr(10, A, BAD, share_out_to_bad),
        tr(12, B, A, 10000),
        tr(20, C, B, 10000),
    ]


# AT-72: A sent 30% of what it sent (3,000 USDT) to a sanctioned address → suspected neighbour,
# confidence 0.3, stored as an inferred label; never a fact.
async def test_at72_neighbour_is_suspected(conn: sqlite3.Connection) -> None:
    sanction(conn, BAD)
    eng, store = engine(conn, Fake(world(3000)))
    t = await eng.run(detect(T))
    a = next(n for n in t.nodes if n.address == A)
    assert (a.terminal, a.test) == ("suspected_malicious", 12)
    assert a.classification is not None
    assert a.classification.confidence == D0("0.3")
    assert t.partition == {"suspected_malicious": D0(1)}  # A's whole share, not expanded further
    (e,) = from_trace(t, lambda x, c: x)
    assert (e.address, e.hop, e.inferred, e.confidence) == (A, 1, True, D0("0.3"))
    assert e.risk_type == "illicit_activity"
    (label,) = store.labels(Chain.BSC, A)
    assert (label.category, label.provenance, label.source_ref) == (
        "suspected_malicious",
        "inferred",
        "derived:freeze-neighbour",
    )


async def test_below_the_share_is_traced_as_usual(conn: sqlite3.Connection) -> None:
    sanction(conn, BAD)
    eng, store = engine(conn, Fake(world(700)))  # 700 of 7,700 sent: 9%
    t = await eng.run(detect(T))
    a = next(n for n in t.nodes if n.address == A)
    assert (a.terminal, a.test) == (None, 11)  # expanded
    assert store.labels(Chain.BSC, A) == []


async def test_stored_label_is_used_without_a_read_until_it_expires(
    conn: sqlite3.Connection,
) -> None:
    sanction(conn, BAD)
    eng, store = engine(conn, Fake(world(3000)))
    await eng.run(detect(T))
    fake = Fake(world(3000))
    eng2, _ = engine(conn, fake)
    t = await eng2.run(detect(T))
    a = next(n for n in t.nodes if n.address == A)
    assert (a.terminal, a.test, a.read) == ("suspected_malicious", 4, False)
    assert a.classification is not None
    assert a.classification.confidence == D0("0.3")  # the stored inference keeps its confidence
    assert A not in fake.asked
    (label,) = store.labels(Chain.BSC, A)
    conn.execute(
        "UPDATE intel_labels SET created_at = ? WHERE id = ?",
        (to_db(NOW - timedelta(days=31)), label.id),
    )
    eng3, _ = engine(conn, Fake(world(3000)))
    t3 = await eng3.run(detect(T))
    a3 = next(n for n in t3.nodes if n.address == A)
    assert a3.test == 12  # expired: retracted, read and derived again
    assert len([x for x in IntelStore(conn).labels(Chain.BSC, A) if x.active]) == 1


async def test_flagged_senders_are_followed_as_facts(conn: sqlite3.Connection) -> None:
    """Tracing in, A's sanctioned *sender* is reached by the trace itself: no suspicion."""
    xs = [tr(5, A, T, 1000), tr(10, BAD, A, 1000)]
    sanction(conn, BAD)
    eng, _ = engine(conn, Fake(xs))
    t = await eng.run(detect(T))
    assert t.partition == {"sanctioned": D0(1)}
