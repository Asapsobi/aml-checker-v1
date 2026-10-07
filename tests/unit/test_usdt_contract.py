"""Tether blacklisted its own USDT contract to lock USDT sent to it by mistake (D-094). Sending
there is an error, not a risk, so the contract is never a frozen counterparty, in exposures or
traces."""

import sqlite3
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from amlcheck.chain.base import Transfer
from amlcheck.chain.cache import ContractCache, TransferCache
from amlcheck.config import Cache, Settings
from amlcheck.core.address import detect
from amlcheck.core.clock import fixed, to_db
from amlcheck.core.models import Chain
from amlcheck.intel.store import IntelStore
from amlcheck.screening.exposure import local_flags
from amlcheck.storage.db import open_db
from amlcheck.trace.engine import TraceEngine
from tests.unit.test_exposure import conn, rules, tr  # noqa: F401 - conn is a fixture
from tests.unit.trace_world import NOW, Fake, NoContracts

CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"  # TRON USDT (data sources §3)
ME = "TVvWhZyLcd2DT2Y78XpyrUS3SyzfLeSsWP"
FROZEN = "TQLK3z7kEGiACUCAXX6zNx7hHvMnEF4cw8"  # a real frozen wallet (blacklisted 2022)


def blacklist(c: sqlite3.Connection, token: str, *who: str) -> None:
    """Tether's own events, as the freeze index stores them: the add, then destroyed funds."""
    for n, (address, event) in enumerate(
        (a, e) for a in who for e in ("AddedBlackList", "DestroyedBlackFunds")
    ):
        c.execute(
            "INSERT INTO issuer_events VALUES ('tron', ?, ?, ?, NULL, ?, 0, ?, ?)",
            (token, address, event, f"own{n}", 100 + n, to_db(NOW - timedelta(days=30))),
        )


async def test_no_exposure_for_sending_to_the_contract(conn: sqlite3.Connection) -> None:  # noqa: F811
    blacklist(conn, "TR7N", "TR7N")
    assert local_flags(conn, Chain.TRON, ["TR7N", "TFROZEN"]) == {"TFROZEN": {"frozen"}}
    found = await rules(conn, [tr(10, "TR7N", "401", out=True), tr(5, "TOK", "50000")])
    assert "R-EXP-01" not in found  # the 401 USDT sent by mistake raises nothing


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / "t.db")


def sent(days_ago: float, frm: str, to: str, amount: int, n: int = 0) -> Transfer:
    return Transfer(
        Chain.TRON,
        f"{abs(hash((frm, to, days_ago, n))):064x}"[:64],
        n,
        None,
        NOW - timedelta(days=days_ago),
        frm,
        to,
        Decimal(amount),
    )


async def test_a_trace_goes_past_the_contract_but_ends_at_a_frozen_wallet(
    db: sqlite3.Connection,
) -> None:
    """Tracing out: the contract is read like any address (it sends nothing on); a frozen wallet
    still ends the path as `frozen` (§7.5 test 3)."""
    blacklist(db, CONTRACT, CONTRACT, FROZEN)
    fake = Fake([sent(5, ME, CONTRACT, 9000), sent(4, ME, FROZEN, 8000)], chain=Chain.TRON)
    eng = TraceEngine(
        db,
        TransferCache(db, {Chain.TRON: fake}, Cache(), clock=fixed(NOW)),
        ContractCache(db, {Chain.TRON: NoContracts()}, clock=fixed(NOW)),
        IntelStore(db, clock=fixed(NOW)),
        Settings(),
        clock=fixed(NOW),
        monotonic=lambda: 0.0,
    )
    t = await eng.run(detect(ME), "out")
    ends = {n.address: n.terminal for n in t.nodes if n.hop == 1}
    assert ends[FROZEN] == "frozen"
    assert ends[CONTRACT] != "frozen"
    assert CONTRACT in {n.address for n in t.nodes}
