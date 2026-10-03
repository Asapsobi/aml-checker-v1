"""`amlcheck check`: screen one address (PRD F1, F3, F4, F6, F11.1).

Exit codes (D-019): 0 NO_HITS, 3 REVIEW, 4 INCOMPLETE, 5 BLOCK, 1 could not run. Invalid input
writes no audit record (AT-07). The audit record is written before anything is printed (engine).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

import httpx
import typer

from amlcheck.chain.base import canonical_amount
from amlcheck.cli import runtime
from amlcheck.core.address import AddressError, detect
from amlcheck.core.clock import to_iso
from amlcheck.core.engine import screen
from amlcheck.core.models import Address, Chain, CheckResult, Verdict
from amlcheck.net.http import Mode

EXIT = {Verdict.NO_HITS: 0, Verdict.REVIEW: 3, Verdict.INCOMPLETE: 4, Verdict.BLOCK: 5}
ACTION = {
    Verdict.BLOCK: "Do not transact. Escalate.",
    Verdict.INCOMPLETE: "A required source failed or is stale. Retry, or treat as REVIEW.",
    Verdict.REVIEW: "Review by hand before transacting.",
    Verdict.NO_HITS: "Proceed per policy. Not a clearance.",
}
DISCLAIMER = (
    "Internal use only. NO_HITS means nothing was found in the sources checked, as of the times "
    "shown; it is not a clearance."
)


def check(
    address: Annotated[str, typer.Argument(help="TRON (T…) or BSC (0x…) address.")],
    chain: Annotated[
        Chain | None, typer.Option(help="Chain for an 0x address (only bsc in v1).")
    ] = None,
    amount: Annotated[str | None, typer.Option(help="USDT amount of the payment.")] = None,
    client: Annotated[str | None, typer.Option(help="Who the check is for.")] = None,
    note: Annotated[str | None, typer.Option(help="Free text kept with the record.")] = None,
    trace: Annotated[
        bool | None,
        typer.Option(
            "--trace/--no-trace",
            help="Trace the source of funds. Default: on when --amount ≥ [trace] auto_amount_usdt.",
        ),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Screen an address: sanctions and issuer freezes, with a recorded verdict."""
    rt = runtime.load()
    try:
        addr = detect(address, chain)
    except AddressError as e:
        runtime.fail(str(e))
    value = _amount(amount)
    conn = runtime.open_database(rt)
    try:
        run_trace = (
            trace
            if trace is not None
            else (value is not None and value >= rt.settings.trace.auto_amount_usdt)
        )
        result = asyncio.run(_screen(rt, conn, addr, value, client, note, run_trace))
    finally:
        conn.close()
    if as_json:
        typer.echo(json.dumps(as_dict(result), indent=2))
    else:
        _print(result)
    raise typer.Exit(EXIT[result.verdict])


def _amount(text: str | None) -> Decimal | None:
    if text is None:
        return None
    try:
        value = Decimal(text.replace(",", "").strip())
    except InvalidOperation:
        runtime.fail(f"--amount is not a number: {text!r}")
    if not value.is_finite() or value < 0:
        runtime.fail(f"--amount must be a positive number: {text!r}")
    return value


async def _screen(
    rt: runtime.Runtime,
    conn: sqlite3.Connection,
    addr: Address,
    amount: Decimal | None,
    client: str | None,
    note: str | None,
    run_trace: bool,
) -> CheckResult:
    async with httpx.AsyncClient() as http_client:
        sources = runtime.make_screening_sources(
            rt, conn, http_client, Mode.CHECK, addr.chain, trace=run_trace
        )
        return await screen(
            addr,
            sources,
            conn=conn,
            settings=rt.settings,
            amount=amount,
            client=client,
            note=note,
            now=rt.clock,
        )


def as_dict(r: CheckResult) -> dict[str, Any]:
    return {
        "check_id": r.check_id,
        "verdict": r.verdict.value,
        "action": ACTION[r.verdict],
        "chain": r.address.chain.value,
        "address": r.address.norm,
        "checked_at": to_iso(r.checked_at),
        "amount_usdt": canonical_amount(r.amount) if r.amount is not None else None,
        "client": r.client,
        "note": r.note,
        "findings": [
            {
                "rule_id": f.rule_id,
                "severity": f.severity.value,
                "source": f.source,
                "summary": f.summary,
                "observed_at": to_iso(f.observed_at),
                "evidence": f.evidence,
            }
            for f in r.findings
        ],
        "sources": [
            {
                "source": s.source,
                "label": s.label,
                "required": s.required,
                "status": s.status.value,
                "detail": s.detail,
                "observed_at": to_iso(s.observed_at),
                "evidence": s.evidence,
            }
            for s in r.sources
        ],
        "audit": {"record_hash": r.record_hash},
        "tool_version": r.tool_version,
        "config_hash": r.config_hash,
        "disclaimer": DISCLAIMER,
    }


def _print(r: CheckResult) -> None:
    echo = typer.echo
    echo(f"{r.verdict.value}  ·  {r.address.chain.value.upper()} {r.address.norm}")
    echo(ACTION[r.verdict])
    if r.findings:
        echo("")
        echo("Findings")
        for f in r.findings:
            low = "  (low priority)" if f.evidence.get("priority") == "low" else ""
            echo(f"  {f.rule_id:<9} {f.severity.value:<10} {f.summary}{low}")
    echo("")
    echo("Sources")
    for s in r.sources:
        need = "" if s.required else "  (not required)"
        echo(f"  {s.status.value:<8} {s.label:<28} {s.detail or ''}{need}")
    exposure = next((s for s in r.sources if s.source == "exposure" and s.evidence), None)
    if exposure is not None:
        _print_exposure(r.address.chain, exposure.evidence)
    echo("")
    extra = f" · {canonical_amount(r.amount)} USDT" if r.amount is not None else ""
    who = f" · client {r.client}" if r.client else ""
    echo(
        f"Check {r.check_id} · {to_iso(r.checked_at)}{extra}{who} · "
        f"audit {r.record_hash[:16]} · amlcheck {r.tool_version}"
    )
    echo(DISCLAIMER)


def _print_exposure(chain: Chain, ev: dict[str, Any]) -> None:
    """History summary and the largest counterparties, full addresses (methodology §4)."""
    echo = typer.echo
    first = ev.get("first_activity") or "none found"
    echo("")
    echo(
        f"History  {ev['transfers']} transfer(s) since {str(ev['since'])[:10]} · received "
        f"{ev['received_usdt']} · sent {ev['sent_usdt']} USDT · first activity {first}"
    )
    if ev.get("zero_value_dropped"):
        echo(f"         {ev['zero_value_dropped']} 0-value transfer(s) dropped (address poisoning)")
    shown = ev.get("counterparties") or []
    if not shown:
        return
    width = 42 if chain is Chain.BSC else 34
    echo("")
    echo(f"Counterparties  (largest {len(shown)} of {ev['counterparty_count']})")
    echo(f"  {'address':<{width}}  {'received':>16}  {'sent':>16}  {'txs':>5}  flags")
    for c in shown:
        flags = ", ".join(c["flags"]) or "-"
        echo(
            f"  {c['address']:<{width}}  {c['received_usdt']:>16}  {c['sent_usdt']:>16}  "
            f"{c['transfers']:>5}  {flags}"
        )
