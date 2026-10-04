"""`amlcheck wallets`: own wallets, the ones `monitor run` watches (PRD F13.1, D-062)."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from amlcheck.cli import runtime
from amlcheck.core.address import AddressError, detect
from amlcheck.core.models import Address, Chain
from amlcheck.intel.store import IntelStore
from amlcheck.monitor import wallets as own

app = typer.Typer(help="Own wallets: monitored for new senders.", no_args_is_help=True)


def _address(text: str, chain: Chain | None = None) -> Address:
    try:
        return detect(text, chain)
    except AddressError as e:
        runtime.fail(str(e))


@app.command()
def add(
    address: Annotated[str, typer.Argument(help="TRON (T…) or BSC (0x…) address.")],
    name: Annotated[str, typer.Option(help="What it is, e.g. 'TRON hot wallet'.")],
    chain: Annotated[Chain | None, typer.Option(help="Chain for an 0x address.")] = None,
) -> None:
    """Register an own wallet (labelled own_or_trusted; monitored)."""
    rt = runtime.load()
    addr = _address(address, chain)
    conn = runtime.open_database(rt)
    try:
        new = own.add(
            conn,
            IntelStore(conn, clock=rt.clock),
            addr,
            name,
            now=rt.clock(),
            by=rt.settings.operator.name or None,
        )
    except ValueError as e:
        runtime.fail(str(e))
    word = "added" if new else "already registered; updated"
    typer.echo(f"{word}: {addr.chain.value} {addr.norm} ({name.strip()})")


@app.command()
def remove(address: Annotated[str, typer.Argument()]) -> None:
    """Stop monitoring an own wallet (its trusted label is retracted)."""
    rt = runtime.load()
    addr = _address(address)
    conn = runtime.open_database(rt)
    store = IntelStore(conn, clock=rt.clock)
    if not own.remove(conn, store, addr, by=rt.settings.operator.name or None):
        runtime.fail(f"{addr.norm} is not an active own wallet")
    typer.echo(f"removed: {addr.chain.value} {addr.norm}")


@app.command("list")
def list_(
    all_: Annotated[bool, typer.Option("--all", help="Include removed ones.")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Own wallets."""
    rt = runtime.load()
    conn = runtime.open_database(rt)
    rows = own.wallets(conn, active_only=not all_)
    seen = {
        (r[0], r[1]): (r[2], r[3])
        for r in conn.execute(
            "SELECT chain, address_norm, last_seen_time, last_run_at FROM monitor_state"
        )
    }
    if as_json:
        out = [
            {
                "chain": w.chain.value,
                "address": w.address,
                "name": w.name,
                "added_at": w.added_at,
                "active": w.active,
                "last_seen_time": seen.get((w.chain.value, w.address), (None, None))[0],
                "last_run_at": seen.get((w.chain.value, w.address), (None, None))[1],
            }
            for w in rows
        ]
        typer.echo(json.dumps(out, indent=2))
        return
    for w in rows:
        state = seen.get((w.chain.value, w.address))
        run = f"monitored up to {state[0][:19]}Z" if state else "not monitored yet"
        off = "" if w.active else "  (removed)"
        typer.echo(f"{w.chain.value:<4} {w.address:<42} {w.name}  ·  {run}{off}")
    typer.echo(f"{len(rows)} own wallet(s)")
