"""`amlcheck cache`: transfer cache stats and prune (PRD F2.7)."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from amlcheck.chain.cache import TransferCache
from amlcheck.cli import runtime
from amlcheck.core.clock import to_iso

app = typer.Typer(help="Inspect or trim the transfer cache.", no_args_is_help=True)


def _cache(rt: runtime.Runtime) -> TransferCache:
    return TransferCache(runtime.open_database(rt), {}, rt.settings.cache, clock=rt.clock)


@app.command()
def stats(
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Addresses, windows and transfers held, per chain."""
    rt = runtime.load()
    s = _cache(rt).stats()
    size = rt.paths.db.stat().st_size if rt.paths.db.exists() else 0
    data = {
        "db": str(rt.paths.db),
        "db_bytes": size,
        "chains": {
            name: {
                "addresses": c.addresses,
                "windows": c.windows,
                "incomplete_windows": c.incomplete_windows,
                "transfers": c.transfers,
            }
            for name, c in s.chains.items()
        },
        "oldest_use": to_iso(s.oldest_use) if s.oldest_use else None,
        "newest_use": to_iso(s.newest_use) if s.newest_use else None,
    }
    if as_json:
        typer.echo(json.dumps(data, indent=2))
        return
    typer.echo(f"db: {data['db']} ({size / 1_048_576:.1f} MB)")
    for name, c in s.chains.items():
        typer.echo(
            f"{name}: {c.addresses} addresses, {c.windows} windows "
            f"({c.incomplete_windows} incomplete), {c.transfers} transfers"
        )
    if s.oldest_use:
        typer.echo(f"last used: oldest {data['oldest_use']}, newest {data['newest_use']}")


@app.command()
def prune(
    days: Annotated[
        int | None,
        typer.Option(
            min=1, help="Forget addresses unused this long. Default: [cache] history_keep_days."
        ),
    ] = None,
) -> None:
    """Forget cached histories of addresses not used recently."""
    rt = runtime.load()
    cache = _cache(rt)
    keep_days = days or rt.settings.cache.history_keep_days
    # P1 keeps nothing special; registry, labels, entities and own wallets join in later phases.
    n = cache.prune(keep_days, keep=lambda chain, address: False)
    left = sum(c.transfers for c in cache.stats().chains.values())
    typer.echo(f"forgot {n} address(es) unused for {keep_days} days; {left} transfers left")
