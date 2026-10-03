"""`amlcheck status`: where amlcheck keeps its data, whether its sources are fresh, the audit head.

A stale sanctions list or TRON freeze index makes checks INCOMPLETE; `status` shows that before a
check does, with the fix (`amlcheck sync`).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from importlib.metadata import version
from typing import Annotated, Any

import httpx
import typer

from amlcheck.cli import runtime
from amlcheck.core.audit import GENESIS
from amlcheck.core.clock import to_iso
from amlcheck.net.http import Http, Mode
from amlcheck.screening.base import SourceHealth
from amlcheck.screening.bsc_freeze import BscFreezeSource
from amlcheck.screening.sanctions import SanctionsSource
from amlcheck.screening.tron_freeze import TronFreezeIndex, TronFreezeSource
from amlcheck.storage.db import load_migrations, schema_version


def status(
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Show the data folder, config, database, source freshness and the audit log head."""
    rt = runtime.load()
    conn = runtime.open_database(rt)
    try:
        db_version = schema_version(conn)
        health = asyncio.run(_health(rt, conn))
        records, head = conn.execute(
            "SELECT count(*), (SELECT record_hash FROM checks ORDER BY seq DESC LIMIT 1) "
            "FROM checks"
        ).fetchone()
    finally:
        conn.close()
    config_hash = rt.settings.hash()
    info: dict[str, Any] = {
        "version": version("amlcheck"),
        "home": str(rt.paths.home),
        "config": str(rt.paths.config),
        "config_found": rt.paths.config.exists(),
        "config_hash": config_hash,
        "db": str(rt.paths.db),
        "db_schema": db_version,
        "db_schema_latest": len(load_migrations()),
        "sources": [
            {
                "source": h.source,
                "label": h.label,
                "status": h.status.value,
                "as_of": to_iso(h.as_of) if h.as_of else None,
                "detail": h.detail,
            }
            for h in health
        ],
        "audit": {"records": records, "head_hash": head or GENESIS},
    }
    if as_json:
        typer.echo(json.dumps(info, indent=2))
        return
    config_note = "" if info["config_found"] else "  (not found: using defaults)"
    echo = typer.echo
    echo(f"amlcheck {info['version']}")
    echo(f"home:    {info['home']}")
    echo(f"config:  {info['config']}{config_note}")
    echo(f"         hash {config_hash[:16]}")
    echo(f"db:      {info['db']}")
    echo(f"         schema {info['db_schema']} (latest {info['db_schema_latest']})")
    echo("sources:")
    for h in health:
        echo(f"  {h.status.value:<8} {h.label:<28} {h.detail}")
    echo(f"audit:   {records} record(s), head {(head or GENESIS)[:16]}")


async def _health(rt: runtime.Runtime, conn: sqlite3.Connection) -> list[SourceHealth]:
    async with httpx.AsyncClient() as client:  # no request is made: health reads local state
        tether = runtime.make_tether(
            rt, Http(client, rt.settings.network, mode=Mode.CHECK), runtime.trongrid_limiter(rt)
        )
        return [
            await SanctionsSource(conn, rt.settings.freshness, clock=rt.clock).health(),
            await TronFreezeSource(
                TronFreezeIndex(tether, conn, clock=rt.clock), rt.settings.freshness, clock=rt.clock
            ).health(),
            await BscFreezeSource(clock=rt.clock).health(),
        ]
