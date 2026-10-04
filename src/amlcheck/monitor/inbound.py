"""`monitor run`: screen new senders to own wallets (PRD F13, D-062 to D-064).

Per active own wallet, oldest first:
1. Read inbound USDT since the wallet's position (`monitor_state.last_seen_time`; the first run
   looks back `[monitor] first_lookback_hours`), through the transfer cache.
2. Group by sender, in order of each sender's first transfer (address as tie-break). Own wallets
   are never screened; a sender screened within `[monitor] rescreen_days` is skipped (AT-53).
3. Screen each remaining sender like `check`: client = the wallet's name, note `monitor run`,
   amount = its largest transfer in the window, so one of `[monitor] trace_amount_usdt` or more is
   traced.
4. At most `[monitor] max_senders_per_run` screens per run (D-063). When the cap stops a wallet,
   its position is set just before the first sender not screened, so the next run starts there;
   senders already screened come round again but are skipped as recently screened.
5. The wallet's position moves to the newest transfer read only when all its senders were handled.

The caller runs this under the run lock (AT-54) and reports and alerts (D-064).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from amlcheck.chain.base import Transfer
from amlcheck.chain.cache import TransferCache
from amlcheck.config import Monitor
from amlcheck.core.address import detect
from amlcheck.core.clock import from_iso, to_db
from amlcheck.core.models import Address, CheckResult, Verdict
from amlcheck.intel import registry
from amlcheck.monitor.wallets import Wallet, is_own
from amlcheck.storage.db import transaction

#: Most transfers read per wallet and run; a busier window is read from its newest end (warned).
READ_LIMIT = 5000
ATTENTION = frozenset({Verdict.REVIEW, Verdict.BLOCK, Verdict.INCOMPLETE})
_TICK = timedelta(microseconds=1)

Screen = Callable[[Address, Decimal, str, bool], Awaitable[CheckResult]]


@dataclass(frozen=True)
class Sender:
    address: str
    first: datetime
    last: datetime
    largest: Decimal
    total: Decimal
    count: int


@dataclass
class WalletRun:
    wallet: Wallet
    since: datetime
    transfers: int = 0
    senders: list[Sender] = field(default_factory=list)
    screened: list[tuple[Sender, CheckResult]] = field(default_factory=list)
    recent: list[Sender] = field(default_factory=list)  # screened within rescreen_days
    own: list[Sender] = field(default_factory=list)
    capped: bool = False
    read_capped: bool = False  # more transfers than READ_LIMIT since the position
    position: datetime | None = None

    @property
    def attention(self) -> list[tuple[Sender, CheckResult]]:
        return [(s, r) for s, r in self.screened if r.verdict in ATTENTION]


def senders(transfers: Iterable[Transfer], wallet: str, after: datetime) -> list[Sender]:
    """Inbound senders after `after`, ordered by their first transfer, then address."""
    by: dict[str, list[Transfer]] = {}
    for t in transfers:
        if t.recipient == wallet and t.sender != wallet and t.amount > 0 and t.time > after:
            by.setdefault(t.sender, []).append(t)
    out = [
        Sender(
            address=a,
            first=min(t.time for t in ts),
            last=max(t.time for t in ts),
            largest=max(t.amount for t in ts),
            total=sum((t.amount for t in ts), Decimal(0)),
            count=len(ts),
        )
        for a, ts in by.items()
    ]
    return sorted(out, key=lambda s: (s.first, s.address))


def position(conn: sqlite3.Connection, wallet: Wallet) -> datetime | None:
    r = conn.execute(
        "SELECT last_seen_time FROM monitor_state WHERE chain = ? AND address_norm = ?",
        (wallet.chain.value, wallet.address),
    ).fetchone()
    return from_iso(r[0]) if r else None


def _save(conn: sqlite3.Connection, wallet: Wallet, seen: datetime, now: datetime) -> None:
    with transaction(conn):
        conn.execute(
            "INSERT INTO monitor_state (chain, address_norm, last_seen_time, last_run_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT (chain, address_norm) DO UPDATE SET "
            "last_seen_time = excluded.last_seen_time, last_run_at = excluded.last_run_at",
            (wallet.chain.value, wallet.address, to_db(seen), to_db(now)),
        )


def _recently_screened(
    conn: sqlite3.Connection, wallet: Wallet, sender: str, now: datetime, days: int
) -> bool:
    cp = registry.get(conn, wallet.chain, sender)
    return cp is not None and from_iso(cp.last_screened_at) > now - timedelta(days=days)


async def run_wallet(
    conn: sqlite3.Connection,
    cache: TransferCache,
    wallet: Wallet,
    settings: Monitor,
    *,
    now: datetime,
    screen: Screen,
    budget: int,
) -> WalletRun:
    """One wallet's share of a run; `budget` is how many screens the run has left."""
    after = position(conn, wallet) or now - timedelta(hours=settings.first_lookback_hours)
    run = WalletRun(wallet, after)
    history = await cache.history(wallet.address, after, now, READ_LIMIT)
    run.read_capped = not history.complete
    run.transfers = sum(1 for t in history.transfers if t.recipient == wallet.address)
    run.senders = senders(history.transfers, wallet.address, after)
    newest = max((t.time for t in history.transfers if t.time > after), default=None)
    for s in run.senders:
        if is_own(conn, wallet.chain, s.address):
            run.own.append(s)
            continue
        if _recently_screened(conn, wallet, s.address, now, settings.rescreen_days):
            run.recent.append(s)
            continue
        if budget - len(run.screened) <= 0:
            run.capped = True
            run.position = s.first - _TICK  # the next run starts with this sender
            _save(conn, wallet, run.position, now)
            return run
        trace = s.largest >= settings.trace_amount_usdt
        result = await screen(detect(s.address, wallet.chain), s.largest, wallet.name, trace)
        run.screened.append((s, result))
    run.position = newest or after
    _save(conn, wallet, run.position, now)
    return run
