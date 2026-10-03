"""The methodology §7.11 worked example as a fake chain (BSC addresses, so any hex is valid)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from amlcheck.chain.base import History, Transfer
from amlcheck.chain.cache import ContractCache, TransferCache
from amlcheck.config import Cache, Settings
from amlcheck.core.clock import fixed, to_db
from amlcheck.core.models import Chain
from amlcheck.intel.store import IntelStore
from amlcheck.net.http import SourceError
from amlcheck.trace.engine import TraceEngine

NOW = datetime(2026, 10, 1, 10, tzinfo=UTC)


def addr(name: str) -> str:
    return "0x" + name.encode().hex().ljust(40, "0")[:40]


T, A, B, C, D, E = (addr(x) for x in ("T", "A", "B", "C", "D", "E"))


def tr(days_ago: float, frm: str, to: str, amount: str | int, n: int = 0) -> Transfer:
    return Transfer(
        Chain.BSC,
        f"0x{abs(hash((frm, to, days_ago, n))):064x}"[:66],
        n,
        None,
        NOW - timedelta(days=days_ago),
        frm,
        to,
        Decimal(amount),
    )


def example() -> list[Transfer]:
    xs = [
        tr(10, A, T, 12000),
        tr(10, B, T, 6000),
        tr(9, C, T, 2000),
        tr(20, D, B, 4000),
        tr(15, E, B, 2000),
    ]
    # E collects 110 small payments before passing 2,000 on to B: a COLLECTOR (0.8).
    xs += [tr(30 - i * 0.09, addr(f"s{i}"), E, 20, i) for i in range(110)]
    return xs


@dataclass
class Fake:
    transfers: list[Transfer]
    fail_on: set[str] = field(default_factory=set)
    asked: list[str] = field(default_factory=list)
    chain: Chain = Chain.BSC

    async def fetch(
        self,
        address: str,
        since: datetime,
        until: datetime | None,
        limit: int,
        *,
        first_activity: bool,
    ) -> History:
        until = until or NOW
        self.asked.append(address)
        if address in self.fail_on:
            raise SourceError("hypersync", "HTTP 503 after 3 tries")
        mine = sorted(
            (
                t
                for t in self.transfers
                if address in (t.sender, t.recipient) and since <= t.time <= until
            ),
            key=lambda t: (-t.time.timestamp(), t.tx_hash),
        )
        return History(tuple(mine[:limit]), since, until, len(mine) <= limit, None, 0)


class NoContracts:
    chain = Chain.BSC

    async def is_contract(self, address: str) -> bool:
        return False


def sanction(conn: sqlite3.Connection, *addresses: str) -> None:
    conn.execute(
        "INSERT INTO list_snapshots (source, fetched_at, published_at, sha256, entry_count, "
        "address_count) VALUES ('ofac_sdn', ?, '2026-09-30', 'h', 1, ?)",
        (to_db(NOW), len(addresses)),
    )
    snap = conn.execute("SELECT max(id) FROM list_snapshots").fetchone()[0]
    for a in addresses:
        conn.execute(
            "INSERT INTO sanctioned_addresses VALUES (?, ?, 'ETH', '1', 'Bad', 'SDGT', 1)",
            (snap, a),
        )


def engine(
    conn: sqlite3.Connection, fake: Fake, settings: Settings | None = None
) -> tuple[TraceEngine, IntelStore]:
    cache = TransferCache(conn, {Chain.BSC: fake}, Cache(), clock=fixed(NOW))
    store = IntelStore(conn, clock=fixed(NOW))
    eng = TraceEngine(
        conn,
        cache,
        ContractCache(conn, {Chain.BSC: NoContracts()}, clock=fixed(NOW)),
        store,
        settings or Settings(),
        clock=fixed(NOW),
        monotonic=lambda: 0.0,
        queries=lambda: len(fake.asked),
    )
    return eng, store


def setup_example(conn: sqlite3.Connection, store: IntelStore) -> None:
    sanction(conn, D)
    binance = store.create_entity(Chain.BSC, "Binance", "exchange_regulated", named_by="sobhan")
    store.link(Chain.BSC, A, binance, "hub", {"why": "operator"}, provenance="operator")
