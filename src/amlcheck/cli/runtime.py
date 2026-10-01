"""The composition root for commands: config, keys, database, one limiter per provider, sources.

Limiters are created here once per process and shared by every source (architecture §5).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import NoReturn

import httpx
import typer

from amlcheck.chain.base import History, HistorySource
from amlcheck.chain.bsc import HyperSyncSource
from amlcheck.chain.tron import TronGridSource
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
from amlcheck.net.http import Http, Mode, SourceError
from amlcheck.net.limits import BudgetPacer, TokenBucket
from amlcheck.storage.db import MigrationError, open_db

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


def make_sources(rt: Runtime, client: httpx.AsyncClient, mode: Mode) -> dict[Chain, HistorySource]:
    http = Http(client, rt.settings.network, mode=mode)
    key = rt.secrets.trongrid_api_key
    tron = TronGridSource(
        http,
        rt.settings.tron,
        api_key=key,
        limiter=TokenBucket(rt.settings.tron.requests_per_second if key else KEYLESS_TRONGRID_RPS),
        clock=rt.clock,
    )
    bsc: HistorySource
    if rt.secrets.hypersync_token:
        bsc = HyperSyncSource(
            http,
            rt.settings.bsc,
            token=rt.secrets.hypersync_token,
            pacer=BudgetPacer(rt.settings.network.max_pacer_wait_seconds),
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
