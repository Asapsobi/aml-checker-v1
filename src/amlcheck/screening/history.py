"""A wallet's history, version 2 (methodology §12.1, D-079, VS-16).

Two steps, so a busy wallet never holds a check:

1. The **required window**, the last `[exposure] lookback_days`, read as in v1 up to
   `[exposure] max_transfers`. If it doesn't fit, the history is incomplete: INCOMPLETE
   (non-negotiable 1).
2. **Older history**, from before USDT existed on the chain, newest first, with what is left of the
   cap, within `[exposure] history_extension_seconds`. Best effort: when it doesn't finish, the
   history simply starts later (`history_from`) and the check says so. A finished read is cached,
   so the next check gets it free.

VS-16: a full read costs TronGrid about one request per 200 transfers, and HyperSync 4–5 queries for
an ordinary BSC wallet but 20–40 s of scanning; an exchange-sized BSC wallet takes many minutes.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from amlcheck.chain.base import History, Transfer
from amlcheck.chain.cache import TransferCache
from amlcheck.config import Exposure
from amlcheck.core.models import Chain

#: Before the first USDT transfer could exist on each chain (TRON mainnet 2018; BSC genesis
#: 2020-08-29). Earlier than needed is harmless: the readers start at the first block.
USDT_FROM = {
    Chain.TRON: datetime(2018, 1, 1, tzinfo=UTC),
    Chain.BSC: datetime(2020, 8, 1, tzinfo=UTC),
}


_MARGIN_S = 5.0  # left for the caller after the older read gives up


@dataclass(frozen=True)
class FullHistory:
    history: History  # newest first; `complete` is the required window's completeness
    required_since: datetime  # start of the required window
    history_from: datetime  # the oldest instant the read covers without a gap
    all_read: bool  # the whole history back to USDT_FROM was read


def _key(t: Transfer) -> tuple[str, int]:
    return (t.tx_hash, t.idx)


async def read(
    cache: TransferCache,
    chain: Chain,
    address: str,
    now: datetime,
    settings: Exposure,
    *,
    deadline: float | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> FullHistory:
    """`deadline` (a `monotonic` time) bounds the older part too, so the caller's own timeout is
    never what stops the read."""
    since = now - timedelta(days=settings.lookback_days)
    required = await cache.history(
        address, since, None, settings.max_transfers, first_activity=True
    )
    room = settings.max_transfers - len(required.transfers)
    extension_seconds = settings.history_extension_seconds
    if deadline is not None:
        extension_seconds = min(extension_seconds, deadline - monotonic() - _MARGIN_S)
    if not required.complete or room <= 0 or extension_seconds <= 0:
        return FullHistory(required, since, since, False)
    try:
        async with asyncio.timeout(extension_seconds):
            older = await cache.history(address, USDT_FROM[chain], since, room)
    except TimeoutError:
        return FullHistory(required, since, since, False)  # older history: not this time
    seen = {_key(t) for t in required.transfers}
    extra = tuple(t for t in older.transfers if _key(t) not in seen)
    transfers = required.transfers + extra  # both newest first, the older part after
    if older.complete:
        history_from, all_read = USDT_FROM[chain], True
    else:  # the cap ran out in the older part: covered from the oldest transfer read
        history_from, all_read = min((t.time for t in extra), default=since), False
    merged = History(
        transfers=transfers,
        since=history_from,
        until=required.until,
        complete=True,
        first_activity=required.first_activity,
        zero_value=required.zero_value + older.zero_value,
    )
    return FullHistory(merged, since, history_from, all_read)
