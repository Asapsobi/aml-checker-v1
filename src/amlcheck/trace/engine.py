"""Source-of-funds trace, version 1 (methodology §7).

Backward ("in") or forward ("out"). Items are processed hop by hop, heavier first, ties by
address; each runs the terminal tests of §7.5 in order, local ones (1–7) before any read (8–10),
and is expanded (11) only when nothing stops it. Every unit of the target's flow ends in exactly
one bucket, a category or an untraced reason (principle P3), so the partition sums to 1.

Weights (§7.3): `w = parent_w × a / parent_in`. A path's bottleneck is its smallest edge amount, an
absolute number; `w × in_T` is only an estimate (D-016). Senders on the item's own path keep their
share and end in `untraced:cycle` (D-046). In a trace, DEPOSIT judges the top recipient from stored
facts only (D-045). A read failure or the time budget makes the trace fail with the partial result
(§7.7); reaching `max_nodes` is declared as `untraced:budget`, not a failure.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from amlcheck.chain.base import History, Transfer
from amlcheck.chain.cache import ContractCache, TransferCache
from amlcheck.config import Settings
from amlcheck.core.clock import Clock, from_iso, utcnow
from amlcheck.core.models import Address, Chain
from amlcheck.intel.categories import BY_NAME
from amlcheck.intel.store import IntelStore
from amlcheck.net.http import SourceError
from amlcheck.profile import classifier as clf
from amlcheck.profile.adapter import SERVICE_CATEGORIES
from amlcheck.profile.classifier import ClassifyContext
from amlcheck.profile.features import profile
from amlcheck.screening.exposure import local_flags
from amlcheck.trace.annotate import layering
from amlcheck.trace.model import (
    TRACE_VERSION,
    UNTRACED,
    Budget,
    Edge,
    Node,
    NodeClass,
    Trace,
    TracePath,
)

Direction = Literal["in", "out"]
MAX_PATHS = 50
TX_SAMPLE = 3
UNFINISHED = "untraced:unfinished"  # only in a failed trace's partial result


@dataclass(frozen=True)
class TraceProgress:
    hop: int
    items_done: int
    items_queued: int
    nodes_read: int
    queries: int
    seconds: float


class TraceFailed(Exception):
    def __init__(self, partial: Trace, reason: str) -> None:
        super().__init__(reason)
        self.partial = partial
        self.reason = reason


@dataclass
class _Flow:
    amount: Decimal = Decimal(0)
    first: datetime | None = None
    last: datetime | None = None
    txs: list[tuple[datetime, str]] = field(default_factory=list)

    def add(self, t: Transfer) -> None:
        self.amount += t.amount
        self.first = t.time if self.first is None else min(self.first, t.time)
        self.last = t.time if self.last is None else max(self.last, t.time)
        self.txs.append((t.time, t.tx_hash))


@dataclass(frozen=True)
class _Item:
    address: str
    hop: int
    weight: Decimal
    path: tuple[str, ...]  # target first, up to the parent
    edge: Edge
    bottleneck: Decimal
    window: tuple[datetime, datetime]


@dataclass
class _State:
    buckets: dict[str, Decimal] = field(default_factory=lambda: defaultdict(Decimal))
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    paths: list[TracePath] = field(default_factory=list)
    read: set[str] = field(default_factory=set)
    cache_hits: int = 0


def flows(address: str, transfers: Iterable[Transfer], direction: Direction) -> dict[str, _Flow]:
    """Counterparties' flows into `address` ("in") or out of it ("out"); self-transfers ignored."""
    out: dict[str, _Flow] = defaultdict(_Flow)
    for t in transfers:
        if t.sender == t.recipient:
            continue
        if direction == "in" and t.recipient == address:
            out[t.sender].add(t)
        elif direction == "out" and t.sender == address:
            out[t.recipient].add(t)
    return dict(out)


def prune(
    candidates: dict[str, _Flow],
    *,
    parent_weight: Decimal,
    parent_flow: Decimal,
    target_flow: Decimal,
    branch: int,
    coverage_share: Decimal,
    min_attributed: Decimal,
) -> tuple[list[tuple[str, _Flow, Decimal]], Decimal]:
    """§7.4: walk senders by amount (ties by address); keep while fewer than `branch` are kept, the
    kept ones cover less than `coverage_share` *before* adding the next, and its estimated
    attributed amount is ≥ `min_attributed`. Returns the kept (address, flow, weight) and the
    weight pruned."""
    ordered = sorted(candidates.items(), key=lambda kv: (-kv[1].amount, kv[0]))
    kept: list[tuple[str, _Flow, Decimal]] = []
    covered = Decimal(0)
    pruned = Decimal(0)
    open_ = True
    for address, flow in ordered:
        weight = parent_weight * flow.amount / parent_flow
        if open_ and (
            len(kept) < branch
            and covered < coverage_share * parent_flow
            and weight * target_flow >= min_attributed
        ):
            kept.append((address, flow, weight))
            covered += flow.amount
        else:
            open_ = False  # sorted by amount: once one is refused, every later one is too
            pruned += weight
    return kept, pruned


class TraceEngine:
    def __init__(
        self,
        conn: sqlite3.Connection,
        cache: TransferCache,
        contracts: ContractCache,
        store: IntelStore,
        settings: Settings,
        *,
        clock: Clock = utcnow,
        monotonic: Callable[[], float] = time.monotonic,
        queries: Callable[[], int] = lambda: 0,
    ) -> None:
        self._conn = conn
        self._cache = cache
        self._contracts = contracts
        self._store = store
        self._s = settings
        self._t = settings.trace
        self._clock = clock
        self._monotonic = monotonic
        self._queries = queries

    async def run(
        self,
        target: Address,
        direction: Direction = "in",
        progress: Callable[[TraceProgress], None] | None = None,
    ) -> Trace:
        as_of = self._clock()
        started = self._monotonic()
        q0 = self._queries()
        st = _State()
        chain = target.chain

        def trace(
            complete: bool = True,
            failure: str | None = None,
            in_t: Decimal = Decimal(0),
            coverage: Decimal | None = None,
        ) -> Trace:
            partition = {k: v for k, v in st.buckets.items() if v}
            return Trace(
                chain=chain,
                target=target.norm,
                direction=direction,
                as_of=as_of,
                target_inflow=in_t,
                nodes=tuple(st.nodes),
                edges=tuple(st.edges),
                partition=partition,
                annotations={"layering": layering(st.nodes)},
                coverage=coverage,
                paths=tuple(
                    sorted(
                        st.paths,
                        key=lambda p: (BY_NAME[p.to_category].order, -p.estimated, p.addresses),
                    )[:MAX_PATHS]
                ),
                budget=Budget(
                    len(st.read), self._queries() - q0, st.cache_hits, self._monotonic() - started
                ),
                complete=complete,
                failure=failure,
                settings={
                    **self._t.model_dump(mode="json"),
                    "trace_version": TRACE_VERSION,
                    "lookback_days": self._s.exposure.lookback_days,
                },
            )

        out_of_time = f"time budget of {self._t.time_budget_seconds} s reached"

        def left() -> float:
            # The budget bounds every read too, not only the gaps between them: one slow read on a
            # bad line must not hold a check past its budget (PRD performance, F9.3).
            return self._t.time_budget_seconds - (self._monotonic() - started)

        since = as_of - timedelta(days=self._s.exposure.lookback_days)
        try:
            async with asyncio.timeout(max(left(), 0)):
                history = await self._read(
                    target.norm, since, as_of, self._s.exposure.max_transfers, st
                )
        except SourceError as e:
            raise TraceFailed(trace(False, f"target {target.norm}: {e.reason}"), e.reason) from None
        except TimeoutError:
            raise TraceFailed(trace(False, out_of_time), out_of_time) from None
        if not history[1]:
            reason = (
                f"target {target.norm} has more than {self._s.exposure.max_transfers} transfers "
                "in its window; the trace can't attribute a partial history"
            )
            raise TraceFailed(trace(False, reason), reason)
        root = flows(target.norm, history[0], direction)
        in_t = sum((f.amount for f in root.values()), Decimal(0))
        st.nodes.append(Node(target.norm, 0, Decimal(1), None, True, None))
        if not in_t:
            return trace(in_t=in_t, coverage=None)

        queue = self._children(
            target.norm, root, Decimal(1), in_t, in_t, 1, (target.norm,), Decimal(-1), direction, st
        )
        hop = 1
        done = 0
        while queue:
            current = sorted(queue, key=lambda i: (-i.weight, i.address, i.path))
            queue = []
            for n, item in enumerate(current):
                if left() <= 0:
                    rest = sum((i.weight for i in current[n:]), Decimal(0))
                    st.buckets[UNFINISHED] += rest
                    raise TraceFailed(trace(False, out_of_time, in_t), out_of_time)
                try:
                    # Cancelling is safe: a step only changes the partition after its last await.
                    async with asyncio.timeout(left()):
                        children = await self._step(item, chain, in_t, direction, st)
                    queue += children
                except TimeoutError:
                    rest = sum((i.weight for i in current[n:]), Decimal(0)) + sum(
                        (i.weight for i in queue), Decimal(0)
                    )
                    st.buckets[UNFINISHED] += rest
                    raise TraceFailed(trace(False, out_of_time, in_t), out_of_time) from None
                except SourceError as e:
                    rest = sum((i.weight for i in current[n:]), Decimal(0)) + sum(
                        (i.weight for i in queue), Decimal(0)
                    )
                    st.buckets[UNFINISHED] += rest
                    reason = f"could not read {item.address} (hop {item.hop}): {e.reason}"
                    raise TraceFailed(trace(False, reason, in_t), reason) from None
                done += 1
                if progress is not None:
                    progress(
                        TraceProgress(
                            hop,
                            done,
                            len(current) - n - 1 + len(queue),
                            len(st.read),
                            self._queries() - q0,
                            self._monotonic() - started,
                        )
                    )
            hop += 1
        coverage = sum((v for k, v in st.buckets.items() if k not in UNTRACED), Decimal(0))
        return trace(in_t=in_t, coverage=coverage)

    # --- one item ---------------------------------------------------------------------------------

    async def _step(
        self, item: _Item, chain: Chain, in_t: Decimal, direction: Direction, st: _State
    ) -> list[_Item]:
        st.edges.append(item.edge)
        terminal = self._local_terminal(item, chain)
        if terminal is not None:
            category, test, cls = terminal
            return self._end(item, category, test, cls, False, in_t, st)
        if item.hop >= self._t.max_hops:
            return self._end(item, "untraced:depth", 6, None, False, in_t, st)
        if item.address not in st.read and len(st.read) >= self._t.max_nodes:
            return self._end(item, "untraced:budget", 7, None, False, in_t, st)
        transfers, complete = await self._read(
            item.address, item.window[0], item.window[1], self._t.hub_transfers, st
        )
        if not complete:  # test 8: too many transfers in the window → a hub
            category = self._entity_kind(chain, item.address) or "service_unattributed"
            return self._end(item, category, 8, NodeClass("HUB", Decimal("0.95")), True, in_t, st)
        read_terminal = await self._classify(item, chain, transfers)
        if read_terminal is not None:
            category, cls = read_terminal
            return self._end(item, category, 9, cls, True, in_t, st)
        senders = flows(item.address, transfers, direction)
        in_n = sum((f.amount for f in senders.values()), Decimal(0))
        if not in_n:
            return self._end(item, "untraced:no_inflow", 10, None, True, in_t, st)
        times = [t.time for t in transfers if item.address in (t.sender, t.recipient)]
        stored = self._conn.execute(
            "SELECT min(first_activity) FROM history_windows WHERE chain = ? AND address_norm = ? "
            "AND first_activity IS NOT NULL",
            (chain.value, item.address),
        ).fetchone()[0]
        # D-048: only what was read (or is already stored); no extra first-activity reads.
        first_seen = min([*times, *([from_iso(stored)] if stored else [])], default=None)
        st.nodes.append(
            Node(
                item.address,
                item.hop,
                item.weight,
                None,
                True,
                item.path[-1],
                11,
                path=item.path,
                n_in=sum(len(f.txs) for f in senders.values()),
                inflow=in_n,
                first_seen=first_seen,
                sent_on=item.edge.amount,
                fresh=first_seen is not None and first_seen >= item.window[0],
            )
        )
        return self._children(
            item.address,
            senders,
            item.weight,
            in_n,
            in_t,
            item.hop + 1,
            (*item.path, item.address),
            item.bottleneck,
            direction,
            st,
        )

    def _children(
        self,
        parent: str,
        senders: dict[str, _Flow],
        parent_weight: Decimal,
        parent_flow: Decimal,
        in_t: Decimal,
        hop: int,
        path: tuple[str, ...],
        parent_bottleneck: Decimal,
        direction: Direction,
        st: _State,
    ) -> list[_Item]:
        kept, pruned = prune(
            senders,
            parent_weight=parent_weight,
            parent_flow=parent_flow,
            target_flow=in_t,
            branch=self._t.branch,
            coverage_share=self._t.coverage_share,
            min_attributed=self._t.min_attributed_usdt,
        )
        st.buckets["untraced:pruned"] += pruned
        days = timedelta(days=self._t.hop_window_days)
        items = []
        for address, flow, weight in kept:
            if flow.first is None or flow.last is None:  # pragma: no cover - a flow has transfers
                continue
            window = (
                (flow.first - days, flow.last)
                if direction == "in"
                else (flow.first, flow.last + days)
            )
            sender, recipient = (address, parent) if direction == "in" else (parent, address)
            edge = Edge(
                sender,
                recipient,
                flow.amount,
                flow.first,
                flow.last,
                tuple(tx for _, tx in sorted(flow.txs)[:TX_SAMPLE]),
            )
            bottleneck = (
                flow.amount if parent_bottleneck < 0 else min(parent_bottleneck, flow.amount)
            )
            items.append(_Item(address, hop, weight, path, edge, bottleneck, window))
        return items

    def _end(
        self,
        item: _Item,
        category: str,
        test: int,
        cls: NodeClass | None,
        read: bool,
        in_t: Decimal,
        st: _State,
    ) -> list[_Item]:
        st.buckets[category] += item.weight
        st.nodes.append(
            Node(
                item.address,
                item.hop,
                item.weight,
                category,
                read,
                item.path[-1],
                test,
                cls,
                path=item.path,
            )
        )
        if category not in UNTRACED:
            st.paths.append(
                TracePath(
                    category,
                    item.hop,
                    (*item.path, item.address),
                    item.bottleneck,
                    item.weight * in_t,
                    test,
                )
            )
        return []

    # --- terminal tests ---------------------------------------------------------------------------

    def _local_terminal(
        self, item: _Item, chain: Chain
    ) -> tuple[str, int, NodeClass | None] | None:
        a = item.address
        if a in item.path:  # 1
            return "untraced:cycle", 1, None
        flags = local_flags(self._conn, chain, [a]).get(a, set())
        if "sanctioned" in flags:  # 2
            return "sanctioned", 2, None
        destroyed = self._conn.execute(
            "SELECT 1 FROM issuer_events WHERE chain = ? AND address_norm = ? "
            "AND event_type = 'DestroyedBlackFunds' LIMIT 1",
            (chain.value, a),
        ).fetchone()
        if "frozen" in flags or destroyed:  # 3
            return "frozen", 3, None
        terminal = self._store.best_terminal(chain, a)  # 4
        if terminal is not None:
            return terminal.category, 4, None
        cached = clf.cached(self._conn, chain, a, self._clock())  # 5
        if cached:
            hit = self._class_terminal(
                chain, a, cached, ("CONTRACT", "HUB", "DEPOSIT", "COLLECTOR")
            )
            if hit is not None:
                return hit[0], 5, hit[1]
        return None

    async def _classify(
        self, item: _Item, chain: Chain, transfers: list[Transfer]
    ) -> tuple[str, NodeClass] | None:
        """Test 9: profile + classify over the item's window; CONTRACT, HUB, DEPOSIT or COLLECTOR
        end the item (HUB as in test 5: a hub found by its counterparties is a service)."""
        now = self._clock()
        h = History(tuple(transfers), item.window[0], item.window[1], True, None, 0)
        p = profile(
            item.address,
            h,
            is_contract=await self._contracts.is_contract(chain, item.address),
            small_usdt=self._s.heuristics.fan_in_small_usdt,
            round_unit_usdt=self._s.classifier.round_unit_usdt,
        )
        cs = clf.classify(
            p, self._stored_context(chain, p.top_recipient, now), self._s.classifier, now
        )
        clf.save(self._conn, chain, p, cs, self._s.classifier, now)
        return self._class_terminal(
            chain, item.address, cs, ("CONTRACT", "HUB", "DEPOSIT", "COLLECTOR")
        )

    def _stored_context(self, chain: Chain, top: str | None, now: datetime) -> ClassifyContext:
        """D-045: what is already known about the top recipient, without reading it."""
        if top is None:
            return ClassifyContext()
        cached = clf.cached(self._conn, chain, top, now) or []
        kinds = {c.type for c in cached}
        terminal = self._store.best_terminal(chain, top)
        entity = self._store.entity_for(chain, top)
        return ClassifyContext(
            top_recipient_is_hub="HUB" in kinds
            or (terminal is not None and terminal.category in SERVICE_CATEGORIES),
            hub_entity_named=entity is not None and entity.named_by is not None,
            top_recipient_fresh_or_pass_through=bool(kinds & {"FRESH", "PASS_THROUGH"}),
        )

    def _class_terminal(
        self,
        chain: Chain,
        address: str,
        cs: list[clf.Classification] | tuple[clf.Classification, ...],
        terminal_types: tuple[str, ...],
    ) -> tuple[str, NodeClass] | None:
        assigned = {c.type: c for c in cs}
        for t in clf.TYPES:
            if t in terminal_types and t in assigned:
                c = assigned[t]
                if t in ("HUB", "DEPOSIT"):
                    category = self._entity_kind(chain, address) or "service_unattributed"
                else:
                    category = clf.TERMINAL[t]
                return category, NodeClass(t, c.confidence)
        return None

    def _entity_kind(self, chain: Chain, address: str) -> str | None:
        entity = self._store.entity_for(chain, address)
        return entity.kind if entity is not None and entity.kind != "unknown" else None

    # --- reading ----------------------------------------------------------------------------------

    async def _read(
        self, address: str, since: datetime, until: datetime, limit: int, st: _State
    ) -> tuple[list[Transfer], bool]:
        before = self._queries()
        h = await self._cache.history(address, since, until, limit)
        st.read.add(address)
        if self._queries() == before:
            st.cache_hits += 1
        return list(h.transfers), h.complete


def hop1_local(trace: Trace) -> dict[str, Decimal]:
    """Weight that ended at tests 2–4 on hop 1 (R-EXP-01's territory; no R-TRC finding, §7.5)."""
    out: dict[str, Decimal] = defaultdict(Decimal)
    for n in trace.nodes:
        if n.hop == 1 and n.test in (2, 3, 4) and n.terminal:
            out[n.terminal] += n.weight
    return dict(out)
