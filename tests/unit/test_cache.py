import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from amlcheck.chain.base import History, Transfer, newest_first
from amlcheck.chain.cache import TransferCache
from amlcheck.config import Cache
from amlcheck.core.models import Chain
from amlcheck.storage.db import open_db

T0 = datetime(2026, 1, 1, tzinfo=UTC)
ME = "0x" + "11" * 20
TRON_ME = "TNHrhtVnRMRaTJFRsqLUqSytCKsavXJJaa"


def at(s: float) -> datetime:
    return T0 + timedelta(seconds=s)


def peer(n: int) -> str:
    return "0x" + format(n, "040x")


@dataclass
class Clock:
    now: datetime = field(default_factory=lambda: at(10_000))

    def __call__(self) -> datetime:
        return self.now


@dataclass
class FakeSource:
    """Answers like a provider: newest `limit` non-zero in the window, 0-value counted."""

    clock: Clock
    transfers: list[Transfer] = field(default_factory=list)
    zero: list[tuple[str, datetime]] = field(default_factory=list)
    first: datetime | None = None
    chain: Chain = Chain.BSC
    asked: list[tuple[datetime, datetime, int, bool]] = field(default_factory=list)

    async def fetch(
        self,
        address: str,
        since: datetime,
        until: datetime | None,
        limit: int,
        *,
        first_activity: bool,
    ) -> History:
        until = until or self.clock()
        self.asked.append((since, until, limit, first_activity))
        mine = newest_first(
            t
            for t in self.transfers
            if address in (t.sender, t.recipient) and since <= t.time <= until
        )
        zero = sum(1 for a, t in self.zero if a == address and since <= t <= until)
        return History(
            mine[:limit],
            since,
            until,
            len(mine) <= limit,
            self.first if first_activity else None,
            zero,
        )


def tr(sec: float, n: int, *, out: bool = False, tx: str | None = None, idx: int = 0) -> Transfer:
    other = peer(n)
    return Transfer(
        Chain.BSC,
        tx or f"0x{int(sec * 1000):x}{n:04x}",
        idx,
        int(sec),
        at(sec),
        ME if out else other,
        other if out else ME,
        Decimal(n) / 7,
    )


def make(tmp: Path, src: FakeSource, ttl: int = 60) -> TransferCache:
    conn = open_db(tmp / "c.db")
    return TransferCache(conn, {Chain.BSC: src}, Cache(target_ttl_seconds=ttl), clock=src.clock)


def brute(
    src: FakeSource, since: datetime, until: datetime, limit: int
) -> tuple[tuple[Transfer, ...], bool]:
    mine = newest_first(
        t for t in src.transfers if ME in (t.sender, t.recipient) and since <= t.time <= until
    )
    return mine[:limit], len(mine) <= limit


# AT-03: the second read asks only for the gap after the first.
async def test_at03_second_read_asks_only_the_gap(tmp_path: Path) -> None:
    clock = Clock(at(1_000))
    src = FakeSource(clock, [tr(100, 1), tr(500, 2), tr(900, 3)])
    cache = make(tmp_path, src)
    h1 = await cache.history(ME, at(0), None, 100)
    assert [t.time for t in h1.transfers] == [at(900), at(500), at(100)]
    assert src.asked == [(at(0), at(1_000), 100, False)]
    src.transfers.append(tr(1_500, 4))
    clock.now = at(2_000)
    h2 = await cache.history(ME, at(0), None, 100)
    assert src.asked[1][:2] == (at(1_000), at(2_000))
    assert len(src.asked) == 2
    assert [t.time for t in h2.transfers] == [at(1_500), at(900), at(500), at(100)]
    assert h2.complete


async def test_d028_recent_tail_is_not_refetched(tmp_path: Path) -> None:
    clock = Clock(at(1_000))
    src = FakeSource(clock, [tr(100, 1)])
    cache = make(tmp_path, src, ttl=60)
    await cache.history(ME, at(0), None, 10)
    clock.now = at(1_059)
    h = await cache.history(ME, at(0), None, 10)
    assert len(src.asked) == 1
    assert h.until == at(1_000)
    clock.now = at(1_061)
    await cache.history(ME, at(0), None, 10)
    assert len(src.asked) == 2


async def test_ttl_never_skips_a_window_with_nothing_cached(tmp_path: Path) -> None:
    clock = Clock(at(1_000))
    src = FakeSource(clock, [tr(990, 1)])
    h = await make(tmp_path, src).history(ME, at(980), None, 10)
    assert len(h.transfers) == 1
    assert src.asked[0][:2] == (at(980), at(1_000))


async def test_fixed_window_inside_cache_asks_nothing(tmp_path: Path) -> None:
    src = FakeSource(Clock(), [tr(s, s) for s in range(100, 1000, 100)])
    cache = make(tmp_path, src)
    await cache.history(ME, at(0), at(5_000), 100)
    h = await cache.history(ME, at(250), at(650), 100)
    assert len(src.asked) == 1
    assert [t.time for t in h.transfers] == [at(600), at(500), at(400), at(300)]


# AT-04: more than the limit → newest `limit`, window marked incomplete.
async def test_at04_limit_marks_incomplete_and_stays_honest(tmp_path: Path) -> None:
    src = FakeSource(Clock(), [tr(s, s) for s in range(100, 1100, 100)])
    cache = make(tmp_path, src)
    h = await cache.history(ME, at(0), at(5_000), 3)
    assert not h.complete
    assert [t.time for t in h.transfers] == [at(1_000), at(900), at(800)]
    row = cache._conn.execute("SELECT since, complete FROM history_windows").fetchone()
    assert row[1] == 0
    h2 = await cache.history(ME, at(0), at(5_000), 3)  # one small probe of the older part
    assert src.asked[1][2] == 1
    assert h2.transfers == h.transfers
    assert not h2.complete
    h3 = await cache.history(ME, at(0), at(5_000), 50)  # a bigger limit reads the rest
    assert h3.complete
    assert len(h3.transfers) == 10
    assert src.asked[-1][:2] == (at(0), at(800))


# AT-05: 0-value transfers dropped and counted.
async def test_at05_zero_value_counted(tmp_path: Path) -> None:
    src = FakeSource(Clock(), [tr(100, 1)], zero=[(ME, at(150)), (ME, at(160))])
    h = await make(tmp_path, src).history(ME, at(0), at(500), 10)
    assert len(h.transfers) == 1
    assert h.zero_value == 2


async def test_shared_transfer_stored_once(tmp_path: Path) -> None:
    t = tr(100, 7)
    src = FakeSource(Clock(), [t])
    cache = make(tmp_path, src)
    await cache.history(ME, at(0), at(500), 10)
    h = await cache.history(peer(7), at(0), at(500), 10)
    assert h.transfers == (t,)
    assert cache.stats().chains["bsc"].transfers == 1
    assert cache.stats().chains["bsc"].addresses == 2


async def test_first_activity_fetched_once(tmp_path: Path) -> None:
    src = FakeSource(Clock(), [tr(100, 1)], first=at(42))
    cache = make(tmp_path, src)
    h = await cache.history(ME, at(0), at(500), 10, first_activity=True)
    assert h.first_activity == at(42)
    assert src.asked[0][3] is True
    h2 = await cache.history(ME, at(0), at(500), 10, first_activity=True)
    assert h2.first_activity == at(42)
    assert len(src.asked) == 1


async def test_first_activity_probe_when_no_gap(tmp_path: Path) -> None:
    src = FakeSource(Clock(), [tr(100, 1)], first=at(7))
    cache = make(tmp_path, src)
    await cache.history(ME, at(0), at(500), 10)
    h = await cache.history(ME, at(0), at(500), 10, first_activity=True)
    assert h.first_activity == at(7)
    assert src.asked[-1] == (at(500), at(500), 1, True)
    await cache.history(ME, at(0), at(500), 10, first_activity=True)
    assert len(src.asked) == 2


async def test_bad_arguments(tmp_path: Path) -> None:
    cache = make(tmp_path, FakeSource(Clock()))
    with pytest.raises(ValueError, match="limit"):
        await cache.history(ME, at(0), at(1), 0)
    with pytest.raises(ValueError, match="before since"):
        await cache.history(ME, at(5), at(1), 10)


async def test_prune_forgets_unused_and_keeps_wanted(tmp_path: Path) -> None:
    clock = Clock(at(0))
    src = FakeSource(clock, [tr(-100, 1), tr(-90, 2)])
    cache = make(tmp_path, src)
    await cache.history(ME, at(-1_000), at(0), 10)
    await cache.history(peer(2), at(-1_000), at(0), 10)
    clock.now = at(31 * 86_400)
    assert cache.prune(30, keep=lambda c, a: a == peer(2)) == 1
    stats = cache.stats().chains["bsc"]
    assert stats.addresses == 1
    assert stats.transfers == 1  # the transfer with peer(2) is still needed
    assert cache.prune(30, keep=lambda c, a: False) == 1
    assert cache.stats().chains["bsc"].transfers == 0


read = st.tuples(
    st.integers(0, 1_200),  # since
    st.one_of(st.none(), st.integers(0, 1_200)),  # until (None = now)
    st.integers(1, 12),  # limit
    st.integers(0, 300),  # clock advance before the read
)


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    times=st.lists(st.integers(0, 1_000), max_size=40),
    reads=st.lists(read, min_size=1, max_size=6),
)
async def test_property_every_read_is_exact(
    times: list[int], reads: list[tuple[int, int | None, int, int]]
) -> None:
    """Any sequence of reads over static data: each answer equals the brute-force answer, and
    `complete` is true exactly when nothing in the window was left out."""
    clock = Clock(at(1_000))
    # Several transfers may share an instant (same second), as on a busy chain.
    src = FakeSource(clock, [tr(s, i + 1) for i, s in enumerate(times)])
    with tempfile.TemporaryDirectory() as d:
        cache = make(Path(d), src)
        for since_s, until_s, limit, advance in reads:
            clock.now += timedelta(seconds=advance)
            since = at(since_s)
            until = at(until_s) if until_s is not None else None
            if until is not None and until < since:
                since, until = until, since
            since = min(since, clock.now)  # a window can't start in the future
            h = await cache.history(ME, since, until, limit)
            want, complete = brute(src, since, h.until, limit)
            assert h.transfers == want
            assert h.complete == complete
