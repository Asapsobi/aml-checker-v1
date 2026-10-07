"""`amlcheck sync`: download the sanctions lists (OFAC, UK, EU) and refresh the TRON freeze index
(PRD F3, F4.1, methodology §13.1).

Run it at least daily: a sanctions list older than 48 h makes every check INCOMPLETE (AT-15).
Runs in background mode, so a TronGrid suspension is waited out rather than failing (D-036).
"""

from __future__ import annotations

import asyncio
import sqlite3

import httpx
import typer

from amlcheck.cli import runtime
from amlcheck.net.http import Http, Mode, SourceError
from amlcheck.screening import lists, sanctions
from amlcheck.screening.tron_freeze import TronFreezeIndex


def sync() -> None:
    """Download the sanctions lists (OFAC, UK, EU) and refresh the Tether TRON freeze index."""
    rt = runtime.load()
    conn = runtime.open_database(rt)
    try:
        ok = asyncio.run(_sync(rt, conn))
    finally:
        conn.close()
    if not ok:
        raise typer.Exit(1)


async def _sync(rt: runtime.Runtime, conn: sqlite3.Connection) -> bool:
    ok = True
    async with httpx.AsyncClient() as client:
        http = Http(client, rt.settings.network, mode=Mode.BACKGROUND)
        typer.echo("OFAC SDN list: downloading (about 3 MB, zipped)…")
        try:
            r = await sanctions.sync(http, conn, rt.settings.ofac, rt.clock)
            if r.accepted:
                before = f", {r.previous_count} before" if r.previous_count is not None else ""
                typer.echo(
                    f"OFAC SDN list: {r.address_count} addresses (list of "
                    f"{r.published_at or 'unknown date'}{before})"
                )
            else:
                typer.echo(f"OFAC SDN list: {r.reason}", err=True)
                ok = False
        except SourceError as e:
            typer.echo(f"error: {e}", err=True)
            ok = False
        for spec, url, on in (
            (lists.UK, rt.settings.lists.uk_url, rt.settings.lists.uk),
            (lists.EU, rt.settings.lists.eu_url, rt.settings.lists.eu),
        ):
            if not on:
                continue
            typer.echo(f"{spec.label}: downloading…")
            try:
                r = await lists.sync_list(
                    http, conn, spec, url, rt.settings.lists.min_kept_share, rt.clock()
                )
            except SourceError as e:
                typer.echo(f"error: {e}", err=True)
                ok = False
                continue
            if r.accepted:
                typer.echo(
                    f"{spec.label}: {r.address_count} TRON/BSC addresses (list of "
                    f"{r.published_at or 'unknown date'})"
                )
            else:
                typer.echo(f"{spec.label}: {r.reason}", err=True)
                ok = False
        typer.echo("Tether TRON freeze index: refreshing…")
        index = TronFreezeIndex(
            runtime.make_tether(rt, http, runtime.trongrid_limiter(rt)), conn, clock=rt.clock
        )
        try:
            res = await index.refresh()
            typer.echo(
                f"Tether TRON freeze index: {res.new_events} new events, up to block "
                f"{res.head_block}"
            )
        except SourceError as e:
            typer.echo(f"error: {e}", err=True)
            ok = False
    return ok
