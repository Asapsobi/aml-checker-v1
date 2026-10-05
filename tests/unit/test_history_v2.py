"""History, version 2: the required window, then older history (methodology §12.1, AT-68)."""

import asyncio
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from amlcheck.chain.base import History, Transfer
from amlcheck.chain.cache import TransferCache
from amlcheck.config import Cache, Exposure
from amlcheck.core.address import detect
from amlcheck.core.clock import fixed
from amlcheck.core.models import Chain, SourceStatus
from amlcheck.core.risk import Exposure as Exp
from amlcheck.screening.history import USDT_FROM, read
from tests.unit.test_cache import Clock, FakeSource
from tests.unit.test_exposure import ME, NOW, conn, source, tr

__all__ = ["conn"]


def years(n: float, other: str, amount: str = "10", *, k: int = 0) -> Transfer:
    return tr(n * 365 * 24, other, amount, n=k)


def cache(conn: sqlite3.Connection, transfers: list[Transfer]) -> TransferCache:
    src = FakeSource(Clock(NOW), transfers, first=NOW - timedelta(days=1200), chain=Chain.TRON)
    return TransferCache(conn, {Chain.TRON: src}, Cache(), clock=fixed(NOW))


# AT-68 (a): 3 years of history, the cap reached at about 2 years: complete, from where it stopped.
async def test_at68_cap_reached_in_older_history(conn: sqlite3.Connection) -> None:
    xs = [years(i / 100, f"TP{i % 9}", k=i) for i in range(300)]  # one every ~3.65 days, 3 years
    full = await read(cache(conn, xs), Chain.TRON, ME, NOW, Exposure(max_transfers=200))
    assert full.history.complete  # the required window was read in full
    assert len(full.history.transfers) == 200
    assert not full.all_read
    oldest = min(t.time for t in full.history.transfers)
    assert full.history_from == oldest
    assert NOW - oldest > timedelta(days=700)  # about 2 years back


# AT-68 (b): the cap reached inside the required 180 days: incomplete → INCOMPLETE.
async def test_at68_cap_inside_required_window(conn: sqlite3.Connection) -> None:
    xs = [tr(i / 10, f"TB{i % 5}", "1", n=i) for i in range(300)]  # 30 hours
    r = await source(conn, xs, exposure=Exposure(max_transfers=200)).check(detect(ME))
    assert r.status is SourceStatus.STALE
    assert "more than 200 transfers since" in (r.detail or "")


async def test_all_history_read_and_old_risk_found(conn: sqlite3.Connection) -> None:
    """The OFAC-address case of VS-15: everything older than 180 days, which v1 never saw."""
    xs = [years(2, "TSANCTIONED", "500"), years(1.5, "TOK", "9500")]
    r = await source(conn, xs).check(detect(ME))
    assert r.status is SourceStatus.OK
    assert r.evidence["all_history"] is True
    assert r.evidence["since"].startswith(USDT_FROM[Chain.TRON].isoformat()[:10])
    assert r.detail == "2 transfer(s) with 2 counterparties, all history"
    (e,) = [Exp.from_json(x) for x in r.evidence["exposures"]]
    assert (e.address, e.percent) == ("TSANCTIONED", Decimal("0.05"))
    assert "R-EXP-01" in {f.rule_id for f in r.findings}


@dataclass
class Slow(FakeSource):
    """Answers the required window at once and anything older too slowly."""

    async def fetch(
        self,
        address: str,
        since: datetime,
        until: datetime | None,
        limit: int,
        *,
        first_activity: bool,
    ) -> History:
        if until is not None and until < NOW - timedelta(days=100):
            await asyncio.sleep(5)
        return await super().fetch(address, since, until, limit, first_activity=first_activity)


async def test_older_read_out_of_time_is_not_a_gap(conn: sqlite3.Connection) -> None:
    xs = [tr(5, "TNEW", "1"), years(2, "TOLD", "1")]
    src = Slow(Clock(NOW), xs, first=NOW - timedelta(days=800), chain=Chain.TRON)
    c = TransferCache(conn, {Chain.TRON: src}, Cache(), clock=fixed(NOW))
    full = await read(
        c, Chain.TRON, ME, NOW, Exposure(history_extension_seconds=0.05), deadline=None
    )
    assert full.history.complete
    assert [t.recipient if t.sender != ME else t.sender for t in full.history.transfers] == [ME]
    assert full.history_from == full.required_since == NOW - timedelta(days=180)
    assert not full.all_read


async def test_deadline_leaves_no_time_for_older(conn: sqlite3.Connection) -> None:
    xs = [tr(5, "TNEW", "1"), years(2, "TOLD", "1")]
    clock = iter([100.0, 100.0])
    full = await read(
        cache(conn, xs),
        Chain.TRON,
        ME,
        NOW,
        Exposure(),
        deadline=103.0,
        monotonic=lambda: next(clock),
    )
    assert len(full.history.transfers) == 1  # 3 s left minus the 5 s margin: none for older
    assert not full.all_read


async def test_second_read_comes_from_the_cache(conn: sqlite3.Connection) -> None:
    xs = [tr(5, "TNEW", "1"), years(2, "TOLD", "1")]
    src = FakeSource(Clock(NOW), xs, first=NOW - timedelta(days=800), chain=Chain.TRON)
    c = TransferCache(conn, {Chain.TRON: src}, Cache(), clock=fixed(NOW))
    first, second = await asyncio.gather(
        read(c, Chain.TRON, ME, NOW, Exposure()), read(c, Chain.TRON, ME, NOW, Exposure())
    )
    assert first.history.transfers == second.history.transfers
    assert first.all_read
    assert second.all_read
    asked = len(src.asked)
    await read(c, Chain.TRON, ME, NOW, Exposure())
    assert len(src.asked) == asked  # all stored: the provider isn't asked again
