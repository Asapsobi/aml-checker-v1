"""`amlcheck watch`: the watchlist (PRD F11.3, D-054, D-057).

`watch run` re-screens every watched address like `check` without an amount (traced when
`[trace] every_check`, D-079), one at a time, under the run lock. A verdict different from the last
one recorded is a change: it is listed, alerted (macOS notification, optional webhook) and the run
exits 6. Every check is in the audit log.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from typing import Annotated, Any

import typer

from amlcheck.cli import check as check_cli
from amlcheck.cli import runtime
from amlcheck.core.address import AddressError, detect
from amlcheck.core.clock import to_iso
from amlcheck.core.models import Address, Chain
from amlcheck.monitor import alerts, watchlist
from amlcheck.storage.lock import Busy, run_lock

app = typer.Typer(help="Watchlist: addresses re-screened by `watch run`.", no_args_is_help=True)
EXIT_CHANGED = 6  # D-019


def _address(text: str, chain: Chain | None = None) -> Address:
    try:
        return detect(text, chain)
    except AddressError as e:
        runtime.fail(str(e))


@app.command()
def add(
    address: Annotated[str, typer.Argument(help="TRON (T…) or BSC (0x…) address.")],
    chain: Annotated[Chain | None, typer.Option(help="Chain for an 0x address.")] = None,
    client: Annotated[str | None, typer.Option(help="Who it is watched for.")] = None,
    note: Annotated[str | None, typer.Option(help="Free text.")] = None,
) -> None:
    """Watch an address."""
    rt = runtime.load()
    addr = _address(address, chain)
    conn = runtime.open_database(rt)
    new = watchlist.add(conn, addr, client, note, rt.clock())
    word = "watching" if new else "already watched; updated"
    typer.echo(f"{word}: {addr.chain.value} {addr.norm}")


@app.command()
def remove(address: Annotated[str, typer.Argument()]) -> None:
    """Stop watching an address."""
    rt = runtime.load()
    addr = _address(address)
    if not watchlist.remove(runtime.open_database(rt), addr):
        runtime.fail(f"{addr.norm} is not watched")
    typer.echo(f"no longer watching {addr.chain.value} {addr.norm}")


@app.command("list")
def list_(
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Watched addresses with their last verdict."""
    rt = runtime.load()
    rows = watchlist.watched(runtime.open_database(rt))
    if as_json:
        typer.echo(json.dumps([_row(w) for w in rows], indent=2))
        return
    for w in rows:
        last = f"{w.last_verdict} at {w.last_checked_at[:19]}Z" if w.last_checked_at else "not run"
        who = f"  {w.client}" if w.client else ""
        typer.echo(f"{w.chain.value:<4} {w.address:<42} {last}{who}")
    typer.echo(f"{len(rows)} watched")


def _row(w: watchlist.Watched) -> dict[str, Any]:
    return {
        "chain": w.chain.value,
        "address": w.address,
        "client": w.client,
        "note": w.note,
        "added_at": w.added_at,
        "last_checked_at": w.last_checked_at,
        "last_verdict": w.last_verdict,
        "last_check_id": w.last_check_id,
    }


@app.command()
def run(
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Re-screen every watched address; exit 6 when a verdict changed."""
    rt = runtime.load()
    try:
        with run_lock(rt.paths.home, "watch run"):
            conn = runtime.open_database(rt)
            try:
                results, changes = _run(rt, conn, as_json)
            finally:
                conn.close()
    except Busy as e:
        runtime.fail(str(e))
    problems = _alert(rt, changes) if changes else []
    if as_json:
        typer.echo(json.dumps({"results": results, "changes": changes}, indent=2))
    else:
        typer.echo(f"{len(results)} watched, {len(changes)} changed")
    for p in problems:
        typer.echo(f"warning: {p}", err=True)
    raise typer.Exit(EXIT_CHANGED if changes else 0)


def _run(
    rt: runtime.Runtime, conn: sqlite3.Connection, quiet: bool
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    results: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []
    for w in watchlist.watched(conn):
        addr = detect(w.address, w.chain)
        traced = runtime.should_trace(rt, None, None)
        r = asyncio.run(check_cli.run_screen(rt, conn, addr, None, w.client, "watch run", traced))
        after = r.verdict.value
        changed = w.last_verdict is not None and w.last_verdict != after
        watchlist.record(conn, addr, after, r.check_id, r.checked_at)
        entry = {
            "chain": addr.chain.value,
            "address": addr.norm,
            "client": w.client,
            "before": w.last_verdict,
            "after": after,
            "score": r.score.shown if r.score else None,
            "check_id": r.check_id,
            "checked_at": to_iso(r.checked_at),
        }
        results.append(entry)
        if changed:
            changes.append(entry)
        if not quiet:
            mark = "CHANGED" if changed else ("new    " if w.last_verdict is None else "same   ")
            was = f"{w.last_verdict} → " if changed else ""
            score = f" · {r.score.shown}" if r.score else ""
            typer.echo(f"{mark} {was}{after}{score}  {addr.chain.value} {addr.norm}")
    return results, changes


def _alert(rt: runtime.Runtime, changes: list[dict[str, Any]]) -> list[str]:
    problems: list[str] = []
    alerts.notify(f"{len(changes)} watched verdict(s) changed: {alerts.summary(changes)}")
    url = rt.settings.monitor.webhook_url
    if url:
        problem = alerts.post(
            url,
            {"source": "amlcheck watch run", "changes": changes},
            timeout=rt.settings.network.timeout_seconds,
        )
        if problem:
            problems.append(problem)
    return problems
