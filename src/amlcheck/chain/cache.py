"""TransferCache: persistent USDT histories with covered windows (PRD F2, AT-03…AT-05).

Every transfer is stored once (shared by sender and recipient). `history_windows` records which time
ranges of an address are fully in `transfers`, so a later read asks the provider only for the gaps.

Window meaning:
- complete: every non-zero transfer of the address in `[since, until]` is stored;
- incomplete (the read hit its limit): `since` is the oldest transfer kept and only `(since, until]`
  is covered; transfers at the instant `since` and before it may be missing.

A read of `[S, U]` with a limit:
1. computes the gaps in coverage (incomplete windows count from just after their `since`);
2. fetches gaps newest first, each with just enough limit to fill the answer, and stops once more
   than `limit` transfers in the range are already stored (the answer is then known to be partial);
3. answers from the database: the newest `limit` non-zero transfers in `[S, U]`, `complete` only if
   nothing was left out (never a silently partial history, PRD F2.4).
With `until=None` a cached tail younger than `[cache] target_ttl_seconds` counts as current (D-028).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from amlcheck.chain.base import History, HistorySource, Transfer, canonical_amount, newest_first
from amlcheck.config import Cache
from amlcheck.core.address import detect
from amlcheck.core.clock import Clock, ensure_utc, from_iso, to_db, utcnow
from amlcheck.core.models import Chain
from amlcheck.storage.db import transaction

_EPSILON = timedelta(microseconds=1)


@dataclass(frozen=True)
class ChainStats:
    addresses: int
    windows: int
    incomplete_windows: int
    transfers: int


@dataclass(frozen=True)
class CacheStats:
    chains: dict[str, ChainStats]
    oldest_use: datetime | None
    newest_use: datetime | None


@dataclass(frozen=True)
class _Window:
    since: datetime
    until: datetime
    complete: bool
    transfer_count: int
    zero_value: int
    first_activity: str | None
    fetched_at: str
    last_used_at: str

    @property
    def covered_from(self) -> datetime:
        return self.since if self.complete else self.since + _EPSILON


@dataclass(frozen=True)
class _Gap:
    start: datetime
    end: datetime
    after_cache: bool  # the coverage right before this gap ends at `start`


class TransferCache:
    def __init__(
        self,
        conn: sqlite3.Connection,
        sources: Mapping[Chain, HistorySource],
        settings: Cache,
        *,
        clock: Clock = utcnow,
    ) -> None:
        self._conn = conn
        self._sources = sources
        self._ttl = timedelta(seconds=settings.target_ttl_seconds)
        self._clock = clock

    async def history(
        self,
        address: str,
        since: datetime,
        until: datetime | None,
        limit: int,
        *,
        first_activity: bool = False,
    ) -> History:
        if limit <= 0:
            raise ValueError("limit must be positive")
        addr = detect(address)
        chain, norm = addr.chain, addr.norm
        source = self._sources[chain]
        now = self._clock()
        since = ensure_utc(since)
        end = ensure_utc(until) if until is not None else now
        if end < since:
            raise ValueError("until is before since")

        gaps = self._gaps(chain, norm, since, end)
        tail = gaps[0] if gaps else None
        recent_tail = tail is not None and tail.after_cache and tail.start >= now - self._ttl
        if until is None and tail is not None and tail.end == end and recent_tail:
            end = tail.start  # D-028: the cached tail is recent enough
            gaps = gaps[1:]

        first = self._stored_first_activity(chain, norm) if first_activity else None
        want_first = first_activity and first is None
        incomplete = False
        for gap in gaps:  # newest first
            newer = self._count(chain, norm, gap.end, end, exclusive_start=True)
            if self._count(chain, norm, gap.end, end, exclusive_start=False) > limit:
                incomplete = True  # more than `limit` already stored: no need to ask
                break
            fetched = await source.fetch(
                norm,
                gap.start,
                gap.end,
                max(1, limit - newer),
                first_activity=want_first,
            )
            self._store(chain, norm, gap, fetched, now)
            if want_first:
                first, want_first = fetched.first_activity, False
            if not fetched.complete:
                incomplete = True
                break
        if want_first:
            probe = await source.fetch(norm, end, end, 1, first_activity=True)
            first = probe.first_activity
            if first is not None:
                with transaction(self._conn):
                    self._conn.execute(
                        "UPDATE history_windows SET first_activity = ? "
                        "WHERE chain = ? AND address_norm = ?",
                        (to_db(first), chain.value, norm),
                    )

        rows = self._read(chain, norm, since, end, limit + 1)
        self._touch(chain, norm, now)
        return History(
            transfers=rows[:limit],
            since=since,
            until=end,
            complete=not incomplete and len(rows) <= limit,
            first_activity=first,
            zero_value=self._zero_value(chain, norm, since, end),
        )

    # --- coverage -------------------------------------------------------------------------------

    def _windows(self, chain: Chain, norm: str) -> list[_Window]:
        rows = self._conn.execute(
            "SELECT since, until, complete, transfer_count, zero_value, first_activity, "
            "fetched_at, last_used_at FROM history_windows "
            "WHERE chain = ? AND address_norm = ? ORDER BY since",
            (chain.value, norm),
        ).fetchall()
        return [
            _Window(from_iso(r[0]), from_iso(r[1]), bool(r[2]), r[3], r[4], r[5], r[6], r[7])
            for r in rows
        ]

    def _gaps(self, chain: Chain, norm: str, since: datetime, end: datetime) -> list[_Gap]:
        """Uncovered parts of `[since, end]`, newest first. Gaps are closed intervals and may share
        an instant with the coverage next to them; re-reading that instant is harmless."""
        gaps: list[_Gap] = []
        covered_to: datetime | None = None  # last instant covered without a break from `since`
        for w in self._windows(chain, norm):
            if w.until < since:
                continue
            frontier = since if covered_to is None else covered_to
            if w.covered_from > frontier or (covered_to is None and w.covered_from > since):
                gaps.append(_Gap(frontier, min(w.since, end), covered_to is not None))
            covered_to = w.until if covered_to is None else max(covered_to, w.until)
            if covered_to >= end:
                break
        if covered_to is None:
            gaps.append(_Gap(since, end, after_cache=False))
        elif covered_to < end:
            gaps.append(_Gap(covered_to, end, after_cache=True))
        return gaps[::-1]

    # --- storage --------------------------------------------------------------------------------

    def _store(self, chain: Chain, norm: str, gap: _Gap, h: History, now: datetime) -> None:
        # An incomplete read was full, so it has an oldest transfer: coverage starts just after it.
        since = gap.start if h.complete else min(t.time for t in h.transfers)
        with transaction(self._conn):
            self._conn.executemany(
                "INSERT OR IGNORE INTO transfers "
                "(chain, tx_hash, idx, block, time, sender, recipient, amount) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        chain.value,
                        t.tx_hash,
                        t.idx,
                        t.block,
                        to_db(t.time),
                        t.sender,
                        t.recipient,
                        canonical_amount(t.amount),
                    )
                    for t in h.transfers
                ],
            )
            new = _Window(
                since=since,
                until=gap.end,
                complete=h.complete,
                transfer_count=len(h.transfers),
                zero_value=h.zero_value,
                first_activity=to_db(h.first_activity) if h.first_activity else None,
                fetched_at=to_db(now),
                last_used_at=to_db(now),
            )
            self._rewrite(chain, norm, [*self._windows(chain, norm), new])

    def _rewrite(self, chain: Chain, norm: str, windows: list[_Window]) -> None:
        """Store `windows` coalesced: touching or overlapping ones merge, the earliest start decides
        `complete` (a complete window first when two start together)."""
        merged: list[_Window] = []
        for w in sorted(windows, key=lambda w: (w.since, not w.complete)):
            if merged and w.since <= merged[-1].until:
                m = merged[-1]
                merged[-1] = _Window(
                    since=m.since,
                    until=max(m.until, w.until),
                    complete=m.complete,
                    transfer_count=0,
                    zero_value=m.zero_value + w.zero_value,
                    first_activity=m.first_activity or w.first_activity,
                    fetched_at=max(m.fetched_at, w.fetched_at),
                    last_used_at=max(m.last_used_at, w.last_used_at),
                )
            else:
                merged.append(w)
        self._conn.execute(
            "DELETE FROM history_windows WHERE chain = ? AND address_norm = ?", (chain.value, norm)
        )
        for m in merged:
            count = self._count(chain, norm, m.since, m.until, exclusive_start=False)
            self._conn.execute(
                "INSERT INTO history_windows (chain, address_norm, since, until, complete, "
                "transfer_count, zero_value, first_activity, fetched_at, last_used_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    chain.value,
                    norm,
                    to_db(m.since),
                    to_db(m.until),
                    int(m.complete),
                    count,
                    m.zero_value,
                    m.first_activity,
                    m.fetched_at,
                    m.last_used_at,
                ),
            )

    def _touch(self, chain: Chain, norm: str, now: datetime) -> None:
        with transaction(self._conn):
            self._conn.execute(
                "UPDATE history_windows SET last_used_at = ? WHERE chain = ? AND address_norm = ?",
                (to_db(now), chain.value, norm),
            )

    # --- queries --------------------------------------------------------------------------------

    def _count(
        self, chain: Chain, norm: str, start: datetime, end: datetime, *, exclusive_start: bool
    ) -> int:
        sql = (
            "SELECT count(*) FROM transfers WHERE chain = ? AND (sender = ? OR recipient = ?) "
            "AND time > ? AND time <= ?"
            if exclusive_start
            else "SELECT count(*) FROM transfers WHERE chain = ? AND (sender = ? OR recipient = ?) "
            "AND time >= ? AND time <= ?"
        )
        row = self._conn.execute(
            sql, (chain.value, norm, norm, to_db(start), to_db(end))
        ).fetchone()
        return int(row[0])

    def _read(
        self, chain: Chain, norm: str, start: datetime, end: datetime, n: int
    ) -> tuple[Transfer, ...]:
        rows = self._conn.execute(
            "SELECT tx_hash, idx, block, time, sender, recipient, amount FROM transfers "
            "WHERE chain = ? AND (sender = ? OR recipient = ?) AND time >= ? AND time <= ? "
            "ORDER BY time DESC, coalesce(block, -1) DESC, tx_hash, idx DESC, sender, recipient, "
            "amount LIMIT ?",
            (chain.value, norm, norm, to_db(start), to_db(end), n),
        ).fetchall()
        return newest_first(
            Transfer(chain, r[0], r[1], r[2], from_iso(r[3]), r[4], r[5], Decimal(r[6]))
            for r in rows
        )

    def _zero_value(self, chain: Chain, norm: str, start: datetime, end: datetime) -> int:
        """0-value transfers dropped in windows overlapping `[start, end]` (whole-window counts)."""
        row = self._conn.execute(
            "SELECT coalesce(sum(zero_value), 0) FROM history_windows "
            "WHERE chain = ? AND address_norm = ? AND since <= ? AND until >= ?",
            (chain.value, norm, to_db(end), to_db(start)),
        ).fetchone()
        return int(row[0])

    def _stored_first_activity(self, chain: Chain, norm: str) -> datetime | None:
        row = self._conn.execute(
            "SELECT min(first_activity) FROM history_windows "
            "WHERE chain = ? AND address_norm = ? AND first_activity IS NOT NULL",
            (chain.value, norm),
        ).fetchone()
        return from_iso(row[0]) if row and row[0] else None

    # --- maintenance ----------------------------------------------------------------------------

    def stats(self) -> CacheStats:
        chains: dict[str, ChainStats] = {}
        for chain in sorted(Chain, key=lambda c: c.value):
            w = self._conn.execute(
                "SELECT count(DISTINCT address_norm), count(*), coalesce(sum(complete = 0), 0) "
                "FROM history_windows WHERE chain = ?",
                (chain.value,),
            ).fetchone()
            t = self._conn.execute(
                "SELECT count(*) FROM transfers WHERE chain = ?", (chain.value,)
            ).fetchone()
            chains[chain.value] = ChainStats(int(w[0]), int(w[1]), int(w[2]), int(t[0]))
        uses = self._conn.execute(
            "SELECT min(last_used_at), max(last_used_at) FROM history_windows"
        ).fetchone()
        return CacheStats(
            chains=chains,
            oldest_use=from_iso(uses[0]) if uses[0] else None,
            newest_use=from_iso(uses[1]) if uses[1] else None,
        )

    def prune(self, keep_days: int, keep: Callable[[Chain, str], bool]) -> int:
        """Forget addresses unused for `keep_days` unless `keep` says otherwise; drop transfers no
        remaining address needs. Returns the number of addresses forgotten."""
        cutoff = to_db(self._clock() - timedelta(days=keep_days))
        stale = self._conn.execute(
            "SELECT chain, address_norm FROM history_windows GROUP BY chain, address_norm "
            "HAVING max(last_used_at) < ? ORDER BY chain, address_norm",
            (cutoff,),
        ).fetchall()
        doomed = [(c, a) for c, a in stale if not keep(Chain(c), a)]
        with transaction(self._conn):
            self._conn.executemany(
                "DELETE FROM history_windows WHERE chain = ? AND address_norm = ?", doomed
            )
            self._conn.execute(
                "DELETE FROM transfers WHERE NOT EXISTS (SELECT 1 FROM history_windows w "
                "WHERE w.chain = transfers.chain "
                "AND w.address_norm IN (transfers.sender, transfers.recipient))"
            )
        return len(doomed)
