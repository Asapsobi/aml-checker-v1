"""The composition root for commands: config, keys, database, one limiter per provider, sources.

Limiters are created here once per process and shared by every source (architecture §5).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import NoReturn

import httpx
import typer

from amlcheck.chain.base import ContractLookup, History, HistorySource
from amlcheck.chain.bsc import BscContractLookup, HyperSyncSource
from amlcheck.chain.cache import ContractCache, TransferCache
from amlcheck.chain.tron import TronContractLookup, TronGridSource
from amlcheck.config import (
    ConfigError,
    Paths,
    Secrets,
    Settings,
    load_secrets,
    load_settings,
    resolve_paths,
)
from amlcheck.core.clock import Clock, from_iso, utcnow
from amlcheck.core.models import Chain
from amlcheck.intel.lookalike import LookalikeSource
from amlcheck.intel.store import IntelStore
from amlcheck.net.http import Http, Limiter, Mode, SourceError
from amlcheck.net.limits import BudgetPacer, TokenBucket
from amlcheck.profile.adapter import ClassifierSource, Profiler
from amlcheck.screening.base import SourceAdapter
from amlcheck.screening.bsc_freeze import BscFreezeSource
from amlcheck.screening.exposure import ExposureSource
from amlcheck.screening.sanctions import SanctionsSource
from amlcheck.screening.tron_freeze import (
    TronBlacklistSource,
    TronFreezeIndex,
    TronFreezeSource,
    TronTether,
)
from amlcheck.storage.db import MigrationError, open_db
from amlcheck.trace.adapter import TraceSource
from amlcheck.trace.engine import TraceEngine
from amlcheck.trace.jobs import TraceJobs

KEYLESS_TRONGRID_RPS = 1.0  # VS-05: without a key TronGrid allows 1 request/s


def fail(message: str) -> NoReturn:
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(1)


@dataclass(frozen=True)
class Runtime:
    paths: Paths
    settings: Settings
    secrets: Secrets
    clock: Clock = utcnow
    # One limiter per provider for the whole process (architecture §5): every source that talks to
    # TronGrid shares one bucket, so together they stay under the key's limit (VS-05, D-036).
    limiters: dict[str, Limiter] = field(default_factory=dict, compare=False)


def load() -> Runtime:
    paths = resolve_paths()
    try:
        settings = load_settings(paths.config)
    except ConfigError as e:
        fail(str(e))
    return Runtime(paths, settings, load_secrets(paths.home))


def open_database(rt: Runtime) -> sqlite3.Connection:
    try:
        return open_db(rt.paths.db)
    except MigrationError as e:
        fail(str(e))


class _Unavailable:
    """Stands in for a source that can't be built (e.g. no token), failing only when used."""

    def __init__(self, chain: Chain, source: str, reason: str) -> None:
        self.chain = chain
        self._source, self._reason = source, reason

    async def fetch(
        self,
        address: str,
        since: datetime,
        until: datetime | None,
        limit: int,
        *,
        first_activity: bool,
    ) -> History:
        raise SourceError(self._source, self._reason)


def trongrid_limiter(rt: Runtime) -> Limiter:
    if "trongrid" not in rt.limiters:
        key = rt.secrets.trongrid_api_key
        rate = rt.settings.tron.requests_per_second if key else KEYLESS_TRONGRID_RPS
        rt.limiters["trongrid"] = TokenBucket(rate)
    return rt.limiters["trongrid"]


def bsc_rpc_limiter(rt: Runtime) -> Limiter:
    if "bsc_rpc" not in rt.limiters:
        rt.limiters["bsc_rpc"] = TokenBucket(rt.settings.bsc.rpc_requests_per_second)
    return rt.limiters["bsc_rpc"]


def make_contract_lookups(rt: Runtime, http: Http) -> dict[Chain, ContractLookup]:
    return {
        Chain.TRON: TronContractLookup(
            http,
            rt.settings.tron,
            api_key=rt.secrets.trongrid_api_key,
            limiter=trongrid_limiter(rt),
        ),
        Chain.BSC: BscContractLookup(http, rt.settings.bsc, limiter=bsc_rpc_limiter(rt)),
    }


def hypersync_pacer(rt: Runtime) -> Limiter:
    if "hypersync" not in rt.limiters:
        rt.limiters["hypersync"] = BudgetPacer(rt.settings.network.max_pacer_wait_seconds)
    return rt.limiters["hypersync"]


def make_tether(rt: Runtime, http: Http, limiter: Limiter) -> TronTether:
    return TronTether(http, rt.settings.tron, api_key=rt.secrets.trongrid_api_key, limiter=limiter)


def make_screening_sources(
    rt: Runtime,
    conn: sqlite3.Connection,
    client: httpx.AsyncClient,
    mode: Mode,
    chain: Chain,
    *,
    trace: bool = False,
) -> list[SourceAdapter]:
    """The sources for a check on `chain` (methodology §2.1); the trace when asked (PRD F9.4)."""
    http = Http(client, rt.settings.network, mode=mode)
    sanctions = SanctionsSource(conn, rt.settings.freshness, clock=rt.clock)
    cache = TransferCache(
        conn, make_sources(rt, client, mode, http=http), rt.settings.cache, clock=rt.clock
    )
    exposure = ExposureSource(
        cache, conn, rt.settings.exposure, rt.settings.heuristics, clock=rt.clock
    )
    lookalike = LookalikeSource(conn, clock=rt.clock)
    store = IntelStore(conn, clock=rt.clock)
    contracts = ContractCache(conn, make_contract_lookups(rt, http), clock=rt.clock)
    profiler = Profiler(
        conn,
        cache,
        contracts,
        store,
        rt.settings.classifier,
        rt.settings.heuristics,
        rt.settings.trace,
        clock=rt.clock,
    )
    classifier = ClassifierSource(profiler, store, rt.settings.classifier, clock=rt.clock)
    extra: list[SourceAdapter] = []
    if trace:
        # The trace gets its own client: it may wait out a suspension (D-036) and its query count
        # is its own, not the other sources running beside it. The cache is shared through `conn`.
        engine = build_trace_engine(rt, conn, client, Mode.BACKGROUND)
        extra.append(
            TraceSource(
                TraceJobs(conn, rt.settings, clock=rt.clock), engine, rt.settings, clock=rt.clock
            )
        )
    if chain is Chain.BSC:
        return [sanctions, BscFreezeSource(clock=rt.clock), exposure, lookalike, classifier, *extra]
    tether = make_tether(rt, http, trongrid_limiter(rt))
    index = TronFreezeIndex(tether, conn, clock=rt.clock)
    return [
        sanctions,
        TronFreezeSource(index, rt.settings.freshness, clock=rt.clock),
        TronBlacklistSource(tether, clock=rt.clock),
        exposure,
        lookalike,
        classifier,
        *extra,
    ]


def make_trace_engine(
    rt: Runtime,
    conn: sqlite3.Connection,
    cache: TransferCache,
    contracts: ContractCache,
    store: IntelStore,
    http: Http,
) -> TraceEngine:
    return TraceEngine(
        conn,
        cache,
        contracts,
        store,
        rt.settings,
        clock=rt.clock,
        queries=lambda: sum(http.sent.values()),
    )


def build_trace_engine(
    rt: Runtime, conn: sqlite3.Connection, client: httpx.AsyncClient, mode: Mode
) -> TraceEngine:
    """A trace engine with its own counted HTTP client (`trace`, and the trace in a check)."""
    http = Http(client, rt.settings.network, mode=mode)
    cache = TransferCache(
        conn, make_sources(rt, client, mode, http=http), rt.settings.cache, clock=rt.clock
    )
    contracts = ContractCache(conn, make_contract_lookups(rt, http), clock=rt.clock)
    return make_trace_engine(rt, conn, cache, contracts, IntelStore(conn, clock=rt.clock), http)


def make_sources(
    rt: Runtime, client: httpx.AsyncClient, mode: Mode, *, http: Http | None = None
) -> dict[Chain, HistorySource]:
    http = http or Http(client, rt.settings.network, mode=mode)
    tron = TronGridSource(
        http,
        rt.settings.tron,
        api_key=rt.secrets.trongrid_api_key,
        limiter=trongrid_limiter(rt),
        clock=rt.clock,
    )
    bsc: HistorySource
    if rt.secrets.hypersync_token:
        bsc = HyperSyncSource(
            http,
            rt.settings.bsc,
            token=rt.secrets.hypersync_token,
            pacer=hypersync_pacer(rt),
            clock=rt.clock,
        )
    else:
        bsc = _Unavailable(Chain.BSC, "hypersync", "no token (set AMLCHECK_HYPERSYNC_TOKEN)")
    return {Chain.TRON: tron, Chain.BSC: bsc}


def parse_when(value: str, now: datetime, *, name: str) -> datetime:
    """`30` (days ago), `2026-09-01` (UTC midnight) or an ISO-8601 time; naive means UTC."""
    text = value.strip()
    if text.isdigit():
        return now - timedelta(days=int(text))
    try:
        dt = from_iso(text) if ("Z" in text or "+" in text[10:]) else datetime.fromisoformat(text)
    except ValueError:
        raise typer.BadParameter(
            f"{name}: not a number of days or an ISO date: {value!r}"
        ) from None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
