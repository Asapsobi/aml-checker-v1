import sqlite3
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from amlcheck.chain.cache import TransferCache
from amlcheck.config import Cache, Monitor, Settings
from amlcheck.core.address import detect
from amlcheck.core.clock import fixed
from amlcheck.core.engine import screen
from amlcheck.core.models import Address, Chain, CheckResult
from amlcheck.intel.lookalike import lookalikes
from amlcheck.intel.store import IntelStore
from amlcheck.monitor import inbound
from amlcheck.monitor import wallets as own
from amlcheck.storage.db import open_db
from tests.unit.test_engine import Fake as Source
from tests.unit.trace_world import NOW, Fake, addr, tr

W = addr("W")  # our wallet
W2 = addr("W2")  # another own wallet
S1, S2, S3 = addr("s1"), addr("s2"), addr("s3")


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / "a.db")


class World:
    def __init__(self, conn: sqlite3.Connection, transfers: list[object]) -> None:
        self.conn = conn
        self.fake = Fake(list(transfers))  # type: ignore[arg-type]
        self.cache = TransferCache(conn, {Chain.BSC: self.fake}, Cache(), clock=fixed(NOW))
        self.rules: dict[str, tuple[str, ...]] = {}
        self.calls: list[tuple[str, Decimal, str, bool]] = []
        store = IntelStore(conn, clock=fixed(NOW))
        own.add(conn, store, detect(W), "hot wallet", now=NOW - timedelta(days=30), by="sobhan")
        own.add(conn, store, detect(W2), "cold wallet", now=NOW - timedelta(days=30), by="sobhan")

    async def screen(self, a: Address, amount: Decimal, wallet: str, trace: bool) -> CheckResult:
        self.calls.append((a.norm, amount, wallet, trace))
        return await screen(
            a,
            [Source("exposure", rules=self.rules.get(a.norm, ()))],
            conn=self.conn,
            settings=Settings(),
            amount=amount,
            client=wallet,
            note="monitor run",
            now=fixed(NOW),
        )

    async def run(self, budget: int = 50) -> inbound.WalletRun:
        (w,) = [x for x in own.wallets(self.conn) if x.address == W]
        return await inbound.run_wallet(
            self.conn, self.cache, w, Monitor(), now=NOW, screen=self.screen, budget=budget
        )


def hours(h: float) -> float:
    return h / 24  # trace_world.tr takes days ago


# AT-52: own wallet receives from a never-screened sender → the next run screens it.
async def test_at52_new_sender_screened(conn: sqlite3.Connection) -> None:
    w = World(
        conn,
        [
            tr(hours(3), S1, W, 50),
            tr(hours(2), S2, W, 20_000),
            tr(hours(1.5), S1, W, 10, 1),
            tr(hours(1), W, S3, 99),  # outgoing: not a sender
            tr(hours(0.5), W2, W, 500),  # from our own cold wallet
            tr(hours(30), S3, W, 70),  # older than the first look-back (24 h)
        ],
    )
    w.rules[S2] = ("R-HEU-02",)
    r = await w.run()
    assert [s.address for s in r.senders] == [S1, S2, W2]  # by first transfer
    assert [x[0] for x in w.calls] == [S1, S2]
    assert w.calls[0] == (S1, Decimal(50), "hot wallet", False)  # amount = largest transfer
    assert w.calls[1] == (S2, Decimal(20_000), "hot wallet", True)  # ≥ 10,000 USDT → traced
    assert [s.address for s in r.own] == [W2]
    assert [(s.address, c.verdict.value) for s, c in r.attention] == [(S2, "REVIEW")]
    assert r.position == NOW - timedelta(hours=0.5)
    assert inbound.position(conn, r.wallet) == r.position
    again = await w.run()
    assert again.senders == []  # nothing new since the position
    assert len(w.calls) == 2


# AT-53: a sender screened 2 days ago (rescreen_days = 7) → skipped.
async def test_at53_recently_screened_sender_skipped(conn: sqlite3.Connection) -> None:
    w = World(conn, [tr(hours(1), S1, W, 50)])
    await screen(
        detect(S1),
        [Source("ofac_sdn")],
        conn=conn,
        settings=Settings(),
        now=fixed(NOW - timedelta(days=2)),
    )
    r = await w.run()
    assert [s.address for s in r.recent] == [S1]
    assert w.calls == []


async def test_rescreen_after_rescreen_days(conn: sqlite3.Connection) -> None:
    w = World(conn, [tr(hours(1), S1, W, 50)])
    await screen(
        detect(S1),
        [Source("ofac_sdn")],
        conn=conn,
        settings=Settings(),
        now=fixed(NOW - timedelta(days=8)),
    )
    r = await w.run()
    assert [x[0] for x in w.calls] == [S1]
    assert r.recent == []


# D-063: the cap stops a wallet before the first sender not screened; the next run starts there.
async def test_cap_resumes_where_it_stopped(conn: sqlite3.Connection) -> None:
    w = World(
        conn,
        [
            tr(hours(3), S1, W, 50),
            tr(hours(2), S2, W, 60),
            tr(hours(1), S3, W, 70),
            tr(hours(0.5), S1, W, 5, 1),
        ],
    )
    first = await w.run(budget=2)
    assert [x[0] for x in w.calls] == [S1, S2]
    assert first.capped
    assert first.position == NOW - timedelta(hours=1) - timedelta(microseconds=1)
    second = await w.run(budget=2)
    assert [x[0] for x in w.calls] == [S1, S2, S3]
    assert [s.address for s in second.recent] == [S1]  # came round again, screened today
    assert not second.capped
    assert second.position == NOW - timedelta(hours=0.5)


def test_own_wallets_store(conn: sqlite3.Connection) -> None:
    store = IntelStore(conn, clock=fixed(NOW))
    assert own.add(conn, store, detect(W), "hot", now=NOW, by="sobhan")
    assert not own.add(conn, store, detect(W), "hot wallet", now=NOW, by="sobhan")  # renamed
    (wallet,) = own.wallets(conn)
    assert wallet.name == "hot wallet"
    labels = [x for x in store.labels(Chain.BSC, W)]
    assert [(x.category, x.source_ref) for x in labels] == [("own_or_trusted", "wallet:hot")]
    assert own.is_own(conn, Chain.BSC, W)
    with pytest.raises(ValueError, match="needs a name"):
        own.add(conn, store, detect(W2), " ", now=NOW, by=None)
    assert own.remove(conn, store, detect(W), by="sobhan")
    assert not own.remove(conn, store, detect(W), by="sobhan")
    assert not own.is_own(conn, Chain.BSC, W)
    assert store.labels(Chain.BSC, W) == []  # retracted
    assert [w.active for w in own.wallets(conn, active_only=False)] == [False]


# D-062: an address imitating an own wallet is flagged by the look-alike guard.
def test_lookalike_of_an_own_wallet(conn: sqlite3.Connection) -> None:
    real = "0x8894e0a0c962cb723c1976a4421c95949be2d4e3"
    fake = "0x8894" + "1" * 32 + "d4e3"
    own.add(conn, IntelStore(conn, clock=fixed(NOW)), detect(real), "hot", now=NOW, by=None)
    (hit,) = lookalikes(conn, detect(fake))
    assert hit == {"address": real, "why_known": "own wallet", "wallet": "hot"}
