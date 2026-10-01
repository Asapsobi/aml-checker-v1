"""`amlcheck history`: an address's USDT transfers, from the cache and the providers (PRD F2)."""

from __future__ import annotations

import asyncio
import json
from typing import Annotated, Any

import httpx
import typer

from amlcheck.chain.base import History, canonical_amount
from amlcheck.chain.cache import TransferCache
from amlcheck.cli import runtime
from amlcheck.core.address import AddressError, detect
from amlcheck.core.clock import to_iso
from amlcheck.core.models import Chain
from amlcheck.net.http import Mode, SourceError


def history(
    address: Annotated[str, typer.Argument(help="TRON (T…) or BSC (0x…) address.")],
    since: Annotated[
        str | None,
        typer.Option(help="Days back, or an ISO date/time. Default: [exposure] lookback_days."),
    ] = None,
    until: Annotated[str | None, typer.Option(help="ISO date/time. Default: now.")] = None,
    limit: Annotated[
        int | None, typer.Option(min=1, help="Most transfers. Default: [exposure] max_transfers.")
    ] = None,
    first_activity: Annotated[
        bool, typer.Option("--first-activity", help="Also find the address's first activity.")
    ] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Show an address's USDT transfers, newest first (0-value spam dropped)."""
    rt = runtime.load()
    try:
        addr = detect(address)
    except AddressError as e:
        runtime.fail(str(e))
    now = rt.clock()
    start = runtime.parse_when(
        since or str(rt.settings.exposure.lookback_days), now, name="--since"
    )
    end = runtime.parse_when(until, now, name="--until") if until else None
    if end is not None and end < start:
        runtime.fail("--until is before --since")
    n = limit or rt.settings.exposure.max_transfers
    conn = runtime.open_database(rt)
    try:
        h = asyncio.run(_read(rt, conn, addr.norm, start, end, n, first_activity))
    except SourceError as e:
        runtime.fail(str(e))
    finally:
        conn.close()
    if as_json:
        typer.echo(json.dumps(_as_json(addr.chain, addr.norm, h, n), indent=2))
    else:
        _print(addr.chain, addr.norm, h, n)


async def _read(
    rt: runtime.Runtime,
    conn: Any,
    address: str,
    since: Any,
    until: Any,
    limit: int,
    first_activity: bool,
) -> History:
    async with httpx.AsyncClient() as client:
        sources = runtime.make_sources(rt, client, Mode.CHECK)
        cache = TransferCache(conn, sources, rt.settings.cache, clock=rt.clock)
        return await cache.history(address, since, until, limit, first_activity=first_activity)


def _direction(address: str, sender: str, recipient: str) -> str:
    if sender == recipient:
        return "self"
    return "out" if sender == address else "in"


def _as_json(chain: Chain, address: str, h: History, limit: int) -> dict[str, Any]:
    return {
        "chain": chain.value,
        "address": address,
        "since": to_iso(h.since),
        "until": to_iso(h.until),
        "limit": limit,
        "complete": h.complete,
        "zero_value_dropped": h.zero_value,
        "first_activity": to_iso(h.first_activity) if h.first_activity else None,
        "transfers": [
            {
                "time": to_iso(t.time),
                "direction": _direction(address, t.sender, t.recipient),
                "counterparty": t.recipient if t.sender == address else t.sender,
                "amount_usdt": canonical_amount(t.amount),
                "tx_hash": t.tx_hash,
                "idx": t.idx,
                "block": t.block,
                "sender": t.sender,
                "recipient": t.recipient,
            }
            for t in h.transfers
        ],
    }


def _print(chain: Chain, address: str, h: History, limit: int) -> None:
    # Plain lines, never truncated: full addresses are what the operator compares (methodology §4).
    echo = typer.echo
    echo(
        f"{chain.value.upper()} {address}  ·  {to_iso(h.since)} → {to_iso(h.until)}  ·  "
        f"{len(h.transfers)} transfer(s), newest first"
    )
    width = 42 if chain is Chain.BSC else 34
    if h.transfers:
        echo(f"{'time (UTC)':<20}  {'dir':<4}  {'counterparty':<{width}}  {'USDT':>22}  tx")
    for t in h.transfers:
        other = t.recipient if t.sender == address else t.sender
        echo(
            f"{to_iso(t.time)[:19] + 'Z':<20}  {_direction(address, t.sender, t.recipient):<4}  "
            f"{other:<{width}}  {canonical_amount(t.amount):>22}  {t.tx_hash}"
        )
    if not h.complete:
        echo(f"Incomplete: more than {limit} transfers in this window; showing the newest {limit}.")
    if h.zero_value:
        echo(f"0-value transfers dropped: {h.zero_value} (address-poisoning spam).")
    if h.first_activity:
        echo(f"First activity: {to_iso(h.first_activity)}")
