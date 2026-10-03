"""Pure pattern functions over a history (methodology §3.5, §5). No I/O, no clock.

Self-transfers are ignored throughout. Ordering is fully tie-broken, so the same transfers always
give the same answer (CLAUDE.md #6).
"""

from __future__ import annotations

from collections import Counter, deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from amlcheck.chain.base import Transfer


@dataclass(frozen=True)
class PassThrough:
    received: Decimal
    passed: Decimal  # received USDT that left within the window after arriving

    @property
    def share(self) -> Decimal | None:
        return self.passed / self.received if self.received else None


def pass_through(address: str, transfers: Iterable[Transfer], window: timedelta) -> PassThrough:
    """FIFO lots: each outgoing transfer spends the oldest received USDT first; what it spends from
    lots that arrived at most `window` earlier counts as passed through. Money held before the
    history starts has no lot, so spending it counts as nothing (methodology §3.5)."""
    lots: deque[tuple[datetime, Decimal]] = deque()
    received = Decimal(0)
    passed = Decimal(0)
    for t in sorted(
        (t for t in transfers if t.sender != t.recipient and address in (t.sender, t.recipient)),
        # At the same instant money arrives before it leaves.
        key=lambda t: (t.time, t.recipient != address, t.tx_hash, t.idx),
    ):
        if t.recipient == address:
            received += t.amount
            lots.append((t.time, t.amount))
            continue
        need = t.amount
        while need > 0 and lots:
            arrived, left = lots[0]
            take = min(need, left)
            if t.time - arrived <= window:
                passed += take
            need -= take
            if left == take:
                lots.popleft()
            else:
                lots[0] = (arrived, left - take)
    return PassThrough(received, passed)


def busiest_window(
    events: Iterable[tuple[datetime, str]], window: timedelta
) -> tuple[int, datetime | None]:
    """The most distinct parties within any `window` (inclusive), and when that window started."""
    ordered = sorted(events)
    best, best_start = 0, None
    counts: Counter[str] = Counter()
    lo = 0
    for t, party in ordered:
        counts[party] += 1
        while t - ordered[lo][0] > window:
            gone = ordered[lo][1]
            counts[gone] -= 1
            if counts[gone] == 0:
                del counts[gone]
            lo += 1
        if len(counts) > best:
            best, best_start = len(counts), ordered[lo][0]
    return best, best_start


def senders(
    address: str,
    transfers: Iterable[Transfer],
    keep: Callable[[Transfer], bool] = lambda t: True,
) -> list[tuple[datetime, str]]:
    return [
        (t.time, t.sender)
        for t in transfers
        if t.recipient == address and t.sender != address and keep(t)
    ]


def recipients(
    address: str,
    transfers: Iterable[Transfer],
    keep: Callable[[Transfer], bool] = lambda t: True,
) -> list[tuple[datetime, str]]:
    return [
        (t.time, t.recipient)
        for t in transfers
        if t.sender == address and t.recipient != address and keep(t)
    ]


def fifo_holds(address: str, transfers: Iterable[Transfer]) -> list[tuple[timedelta, Decimal]]:
    """How long each received amount stayed before it was spent, FIFO (methodology §5
    `median_hold_hours`). Amounts still held at the end, and spending of money held before the
    history starts, produce nothing."""
    lots: deque[tuple[datetime, Decimal]] = deque()
    holds: list[tuple[timedelta, Decimal]] = []
    for t in sorted(
        (t for t in transfers if t.sender != t.recipient and address in (t.sender, t.recipient)),
        key=lambda t: (t.time, t.recipient != address, t.tx_hash, t.idx),
    ):
        if t.recipient == address:
            lots.append((t.time, t.amount))
            continue
        need = t.amount
        while need > 0 and lots:
            arrived, left = lots[0]
            take = min(need, left)
            holds.append((t.time - arrived, take))
            need -= take
            if left == take:
                lots.popleft()
            else:
                lots[0] = (arrived, left - take)
    return holds
