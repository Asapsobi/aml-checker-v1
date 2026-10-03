import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from amlcheck.chain.base import Transfer
from amlcheck.chain.cache import ContractCache, TransferCache
from amlcheck.config import Cache, Classifier, Heuristics, Trace
from amlcheck.core.address import detect
from amlcheck.core.clock import fixed
from amlcheck.core.models import Chain, Severity, SourceStatus
from amlcheck.intel.store import IntelStore, NewLabel
from amlcheck.profile.adapter import ClassifierSource, Profiler
from amlcheck.storage.db import open_db
from tests.unit.test_cache import Clock, FakeSource

NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)
HUB = "0x" + "a1" * 20
DEP = "0x" + "d0" * 20
COL = "0x" + "c0" * 20


def a(n: int) -> str:
    return "0x" + format(n, "040x")


def tr(hours_ago: float, frm: str, to: str, amount: str, n: int = 0) -> Transfer:
    return Transfer(
        Chain.BSC,
        f"0x{abs(hash((frm, to, hours_ago, n))):x}",
        n,
        None,
        NOW - timedelta(hours=hours_ago),
        frm,
        to,
        Decimal(amount),
    )


class NoContracts:
    chain = Chain.BSC

    def __init__(self) -> None:
        self.asked: list[str] = []

    async def is_contract(self, address: str) -> bool:
        self.asked.append(address)
        return False


def world() -> list[Transfer]:
    xs = [tr(100 + i, a(10_000 + i), HUB, "50", i) for i in range(1_100)]  # busy hub: capped
    xs += [tr(30 - i, a(500 + i), DEP, "1000", i) for i in range(8)]  # 8 senders into the deposit
    xs.append(tr(20, DEP, HUB, "7960"))  # 99.5% swept to the hub within hours
    return xs


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / "a.db")


def make(conn: sqlite3.Connection, xs: list[Transfer]) -> tuple[Profiler, IntelStore, NoContracts]:
    src = FakeSource(Clock(NOW), xs, chain=Chain.BSC)
    cache = TransferCache(conn, {Chain.BSC: src}, Cache(), clock=fixed(NOW))
    lookups = NoContracts()
    store = IntelStore(conn, clock=fixed(NOW))
    profiler = Profiler(
        conn,
        cache,
        ContractCache(conn, {Chain.BSC: lookups}, clock=fixed(NOW)),
        store,
        Classifier(),
        Heuristics(),
        Trace(),
        clock=fixed(NOW),
    )
    return profiler, store, lookups


async def test_hub_is_capped(conn: sqlite3.Connection) -> None:
    profiler, _, _ = make(conn, world())
    r = await profiler.classify(Chain.BSC, HUB)
    assert {c.type: c.confidence for c in r.classifications}["HUB"] == Decimal("0.95")
    assert r.profile.capped


# AT-33: a deposit sweeping 99.5% to a hub within 6 h from 8 senders; then name the hub's entity
# "Binance" exchange_regulated → DEPOSIT linked to the hub's entity; both resolve to
# exchange_regulated.
async def test_at33_deposit_linked_and_named(conn: sqlite3.Connection) -> None:
    profiler, store, _ = make(conn, world())
    r = await profiler.classify(Chain.BSC, DEP)
    kinds = {c.type: c.confidence for c in r.classifications}
    assert kinds["DEPOSIT"] == Decimal("0.9")  # base + ≥5 senders + hold ≤12 h + share ≥0.99
    assert r.context.top_recipient_is_hub
    hub_entity = store.entity_for(Chain.BSC, HUB)
    dep_entity = store.entity_for(Chain.BSC, DEP)
    assert hub_entity is not None
    assert dep_entity is not None
    assert hub_entity.id == dep_entity.id == r.entity_id
    assert hub_entity.name == f"hub-{HUB[:8]}"
    assert (hub_entity.role, dep_entity.role) == ("hub", "deposit")
    store.name_entity(hub_entity.id, "Binance", "exchange_regulated", by="sobhan")
    for address in (HUB, DEP):
        t = store.best_terminal(Chain.BSC, address)
        assert t is not None
        assert t.category == "exchange_regulated"
    again = await profiler.classify(Chain.BSC, DEP)
    assert {c.type: c.confidence for c in again.classifications}["DEPOSIT"] == Decimal("1.0")


async def test_top_recipient_classification_is_reused(conn: sqlite3.Connection) -> None:
    profiler, _, lookups = make(conn, world())
    await profiler.classify(Chain.BSC, DEP)
    await profiler.classify(Chain.BSC, DEP)
    assert lookups.asked.count(HUB) == 1  # hub profiled once; then its stored result is used


async def test_operator_membership_is_not_overwritten(conn: sqlite3.Connection) -> None:
    profiler, store, _ = make(conn, world())
    mine = store.create_entity(Chain.BSC, "our OTC desk", "otc_desk", named_by="sobhan")
    store.link(Chain.BSC, DEP, mine, "member", {"why": "ours"}, provenance="operator")
    r = await profiler.classify(Chain.BSC, DEP)
    assert r.entity_id is None
    entity = store.entity_for(Chain.BSC, DEP)
    assert entity is not None
    assert entity.id == mine


def collector_world() -> list[Transfer]:
    xs = [tr(48 - i * 0.3, a(2_000 + i), COL, "20" if i % 25 else "500", i) for i in range(120)]
    xs.append(tr(1, COL, a(9_999), "2200"))  # 85%+ to one address
    xs.append(tr(0.5, COL, a(9_998), "300"))
    return xs


# AT-34 through the source: COLLECTOR ≥ 0.7 → R-HEU-07, low priority, REVIEW.
async def test_at34_collector_raises_heu07(conn: sqlite3.Connection) -> None:
    profiler, store, _ = make(conn, collector_world())
    r = await ClassifierSource(profiler, store, Classifier(), clock=fixed(NOW)).check(detect(COL))
    assert r.status is SourceStatus.OK
    (f,) = r.findings
    assert (f.rule_id, f.severity) == ("R-HEU-07", Severity.REVIEW)
    assert f.evidence["priority"] == "low"
    assert f.evidence["type"] == "COLLECTOR"
    assert Decimal(f.evidence["confidence"]) >= Decimal("0.7")


async def test_operator_label_wins_over_inference(conn: sqlite3.Connection) -> None:
    profiler, store, _ = make(conn, collector_world())
    store.add_label(NewLabel(Chain.BSC, COL, "payment_processor", "operator", "case:9"))
    r = await ClassifierSource(profiler, store, Classifier(), clock=fixed(NOW)).check(detect(COL))
    assert r.status is SourceStatus.SKIPPED
    assert r.findings == ()
    assert "label wins" in (r.detail or "")
