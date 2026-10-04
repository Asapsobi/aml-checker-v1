"""`amlcheck monitor run`: screen new senders to own wallets (PRD F13, D-062 to D-064).

Runs under the shared run lock: a second run at the same time stops at once, "already running", and
screens nothing (AT-54). Exit 6 when a sender screened in this run is REVIEW, BLOCK or INCOMPLETE,
with the same alerts as `watch run` (macOS notification, optional webhook); 0 otherwise.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from decimal import Decimal
from typing import Annotated, Any

import httpx
import typer

from amlcheck.chain.base import canonical_amount, usdt
from amlcheck.chain.cache import TransferCache
from amlcheck.cli import check as check_cli
from amlcheck.cli import runtime
from amlcheck.core.clock import to_iso
from amlcheck.core.models import Address, CheckResult
from amlcheck.monitor import alerts, inbound
from amlcheck.monitor import wallets as own
from amlcheck.net.http import Mode
from amlcheck.storage.lock import Busy, run_lock

app = typer.Typer(help="Monitor own wallets for new senders.", no_args_is_help=True)
EXIT_ATTENTION = 6  # D-019, D-064


@app.command()
def run(
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Screen new senders to every active own wallet; exit 6 when one needs attention."""
    rt = runtime.load()
    try:
        with run_lock(rt.paths.home, "monitor run"):
            conn = runtime.open_database(rt)
            try:
                wallets = own.wallets(conn)
                runs = asyncio.run(_run(rt, conn, wallets)) if wallets else []
            finally:
                conn.close()
    except Busy:
        runtime.fail("already running: another batch, watch or monitor run holds the lock")
    if not runs:
        typer.echo("no own wallets: add one with `amlcheck wallets add <address> --name …`")
        raise typer.Exit(0)
    attention = [_entry(r, s, c) for r in runs for s, c in r.attention]
    problems = _alert(rt, attention) if attention else []
    if as_json:
        typer.echo(
            json.dumps({"wallets": [_wallet(r) for r in runs], "attention": attention}, indent=2)
        )
    else:
        _print(runs)
    for p in problems:
        typer.echo(f"warning: {p}", err=True)
    raise typer.Exit(EXIT_ATTENTION if attention else 0)


async def _run(
    rt: runtime.Runtime, conn: sqlite3.Connection, wallets: list[own.Wallet]
) -> list[inbound.WalletRun]:
    async with httpx.AsyncClient() as client:
        cache = TransferCache(
            conn,
            runtime.make_sources(rt, client, Mode.BACKGROUND),
            rt.settings.cache,
            clock=rt.clock,
        )

        async def screen(addr: Address, amount: Decimal, wallet: str, trace: bool) -> CheckResult:
            return await check_cli.run_screen(rt, conn, addr, amount, wallet, "monitor run", trace)

        budget = rt.settings.monitor.max_senders_per_run
        runs = []
        for w in wallets:
            r = await inbound.run_wallet(
                conn,
                cache,
                w,
                rt.settings.monitor,
                now=rt.clock(),
                screen=screen,
                budget=budget,
                every_check=rt.settings.trace.every_check,
            )
            budget -= len(r.screened)
            runs.append(r)
    return runs


def _entry(r: inbound.WalletRun, s: inbound.Sender, c: CheckResult) -> dict[str, Any]:
    return {
        "wallet": r.wallet.name,
        "chain": r.wallet.chain.value,
        "sender": s.address,
        "verdict": c.verdict.value,
        "score": c.score.shown if c.score else None,
        "largest_usdt": canonical_amount(s.largest),
        "total_usdt": canonical_amount(s.total),
        "transfers": s.count,
        "check_id": c.check_id,
        "checked_at": to_iso(c.checked_at),
        # The `alerts.summary` shape, shared with `watch run`.
        "address": s.address,
        "before": "new sender",
        "after": c.verdict.value,
    }


def _wallet(r: inbound.WalletRun) -> dict[str, Any]:
    return {
        "wallet": r.wallet.name,
        "chain": r.wallet.chain.value,
        "address": r.wallet.address,
        "since": to_iso(r.since),
        "position": to_iso(r.position) if r.position else None,
        "inbound_transfers": r.transfers,
        "new_senders": len(r.senders),
        "screened": [_entry(r, s, c) for s, c in r.screened],
        "skipped_recently_screened": [s.address for s in r.recent],
        "skipped_own_wallets": [s.address for s in r.own],
        "capped": r.capped,
        "read_capped": r.read_capped,
    }


def _print(runs: list[inbound.WalletRun]) -> None:
    echo = typer.echo
    total = 0
    for r in runs:
        w = r.wallet
        echo(
            f"{w.name} ({w.chain.value} {w.address[:8]}…{w.address[-6:]}): "
            f"{r.transfers} inbound since {to_iso(r.since)[:19]}Z, {len(r.senders)} sender(s), "
            f"{len(r.screened)} screened, {len(r.recent)} screened recently"
        )
        for s, c in r.screened:
            mark = "ATTENTION" if c.verdict in inbound.ATTENTION else "ok       "
            score = f" · {c.score.shown}" if c.score else ""
            echo(
                f"  {mark} {c.verdict.value}{score}  {s.address}  "
                f"(largest {usdt(s.largest)} USDT, {s.count} transfer(s))"
            )
        if r.capped:
            echo("  capped at [monitor] max_senders_per_run; the next run continues from here")
        if r.read_capped:
            echo(f"  more than {inbound.READ_LIMIT} transfers since the last run: the newest read")
        total += len(r.attention)
    echo(f"{sum(len(r.screened) for r in runs)} sender(s) screened, {total} need attention")


def _alert(rt: runtime.Runtime, attention: list[dict[str, Any]]) -> list[str]:
    problems: list[str] = []
    alerts.notify(f"{len(attention)} new sender(s) need attention: {alerts.summary(attention)}")
    url = rt.settings.monitor.webhook_url
    if url:
        problem = alerts.post(
            url,
            {"source": "amlcheck monitor run", "attention": attention},
            timeout=rt.settings.network.timeout_seconds,
        )
        if problem:
            problems.append(problem)
    return problems
