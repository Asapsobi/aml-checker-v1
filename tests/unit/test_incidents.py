"""Public incidents that reveal a designated entity's wallets (methodology §13.5, D-101, VS-24):
Nobitex's deposit addresses drained into the attackers' burn address on 2025-06-18."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from amlcheck.chain.base import Transfer
from amlcheck.config import Intel, Settings
from amlcheck.core.address import detect
from amlcheck.core.clock import fixed
from amlcheck.core.models import Chain, Severity
from amlcheck.core.risk import from_trace
from amlcheck.intel.incidents import BY_ID, build, built, incident_for
from amlcheck.intel.names import entity_name
from amlcheck.screening.designated import DesignatedSource
from amlcheck.storage.db import open_db
from tests.unit.trace_world import NOW, Fake, T, addr, engine, tr

NOBITEX = BY_ID["nobitex_2025_06"]
FIX = Path(__file__).parents[1] / "fixtures" / "trongrid" / "nobitex_drain_sample.json"
DRAINED = "TCFnNHJw3Pg3zr3H1gEazwXnpu2J9zfwir"  # sent USDT 7,004,321 at 04:29:21 (VS-24)


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / "t.db")


def drain_rows() -> list[Transfer]:
    """The recorded TronGrid rows (VS-24) as transfers."""
    out = []
    for n, x in enumerate(json.loads(FIX.read_text())["data"]):
        out.append(
            Transfer(
                Chain.TRON,
                x["transaction_id"],
                n,
                None,
                datetime.fromtimestamp(x["block_timestamp"] / 1000, UTC),
                x["from"],
                x["to"],
                Decimal(x["value"]) / Decimal(10**6),
            )
        )
    return out


def test_the_drain_is_indexed_once_per_sender(conn: sqlite3.Connection) -> None:
    rows = drain_rows()
    late = Transfer(Chain.TRON, "f" * 64, 0, None, NOBITEX.until + timedelta(hours=1),
                    addr("late"), NOBITEX.sink, Decimal(5))  # fmt: skip
    b = build(conn, NOBITEX, [*rows, late])
    # The first row (1 USDT, alone in its block) is not in a batch; its sender still counts through
    # its 7,004,321 USDT in a batch of 4 a minute later. The late one is outside the window.
    assert (b.transfers, b.addresses) == (4, 4)
    assert b.usdt == sum((r.amount for r in rows[1:]), Decimal(0))
    assert built(conn, NOBITEX) == b
    assert rows[0].sender == DRAINED
    assert incident_for(conn, Chain.TRON, DRAINED) is NOBITEX
    assert incident_for(conn, Chain.TRON, addr("late")) is None
    lone = Transfer(Chain.TRON, "e" * 64, 0, None, rows[1].time + timedelta(seconds=30),
                    addr("lone"), NOBITEX.sink, Decimal("0.5"))  # fmt: skip
    build(conn, NOBITEX, [*rows, lone])
    assert incident_for(conn, Chain.TRON, addr("lone")) is None  # alone in its block: an outsider
    assert NOBITEX.designation.text == "Nobitex (OFAC SDN 56981)"
    assert entity_name(conn, Chain.TRON, DRAINED, "sanctioned_entity") == "Nobitex (OFAC SDN 56981)"


def index(conn: sqlite3.Connection, *addresses: str, chain: Chain = Chain.BSC) -> None:
    conn.executemany(
        "INSERT INTO incident_addresses VALUES ('nobitex_2025_06', ?, ?, '2025-06-18', '1')",
        [(chain.value, a) for a in addresses],
    )


async def test_a_drained_wallet_ends_a_trace_at_any_hop(conn: sqlite3.Connection) -> None:
    """Nobitex's deposit addresses kept their balances: the trace reads them as personal wallets
    with no outflow. The index says whose they were, at any hop, inferred at 0.9."""
    near, mid, far = addr("near"), addr("mid"), addr("far")
    index(conn, near, far)
    xs = [tr(5, near, T, 600), tr(6, mid, T, 400), tr(8, far, mid, 400), tr(9, addr("x"), far, 9)]
    eng, _ = engine(conn, Fake(xs), Settings())
    t = await eng.run(detect(T))
    ends = {n.address: (n.hop, n.terminal, n.test, n.entity) for n in t.nodes if n.test == 12}
    assert ends == {
        near: (1, "sanctioned_entity", 12, "Nobitex (OFAC SDN 56981)"),
        far: (2, "sanctioned_entity", 12, "Nobitex (OFAC SDN 56981)"),
    }
    by = {e.address: e for e in from_trace(t, lambda a, c: "not used")}
    assert (by[near].exposure_type, by[near].confidence, by[near].inferred) == (
        "direct",
        Decimal("0.9"),
        True,
    )
    assert by[far].exposure_type == "indirect"


async def test_incidents_can_be_turned_off(conn: sqlite3.Connection) -> None:
    near = addr("near")
    index(conn, near)
    eng, _ = engine(conn, Fake([tr(5, near, T, 600)]), Settings(intel=Intel(incidents=False)))
    t = await eng.run(detect(T))
    assert [n.test for n in t.nodes if n.address == near] != [12]


async def test_the_checked_address_drained_in_the_hack(conn: sqlite3.Connection) -> None:
    index(conn, DRAINED, chain=Chain.TRON)
    r = await DesignatedSource(conn, None, clock=fixed(NOW)).check(detect(DRAINED))
    (f,) = r.findings
    assert (f.rule_id, f.severity) == ("R-SAN-02", Severity.REVIEW)
    assert f.evidence["incident"] == "nobitex_2025_06"
    assert "inferred, 0.9" in f.summary
    (e,) = r.evidence["exposures"]
    assert (e["entity"], e["inferred"], e["confidence"]) == (
        "Nobitex (OFAC SDN 56981)",
        True,
        "0.9",
    )
