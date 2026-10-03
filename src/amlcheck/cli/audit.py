"""`amlcheck audit`: list and verify the audit log (PRD F6.3, F6.5). Export comes in P8."""

from __future__ import annotations

import json
from typing import Annotated, Any

import typer

from amlcheck.cli import runtime
from amlcheck.core import audit as chain
from amlcheck.core.address import AddressError, detect
from amlcheck.core.clock import to_db
from amlcheck.core.models import Verdict

app = typer.Typer(help="List and verify the audit log.", no_args_is_help=True)


@app.command("list")
def list_(
    limit: Annotated[int, typer.Option(min=1, help="Most records, newest first.")] = 20,
    address: Annotated[str | None, typer.Option(help="Only this address.")] = None,
    verdict: Annotated[Verdict | None, typer.Option(help="Only this verdict.")] = None,
    client: Annotated[str | None, typer.Option(help="Only this client (any case).")] = None,
    since: Annotated[str | None, typer.Option(help="Days back, or an ISO date/time.")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Recorded checks, newest first."""
    rt = runtime.load()
    conn = runtime.open_database(rt)
    chain_value = norm = None
    if address:
        try:
            a = detect(address)
        except AddressError as e:
            runtime.fail(str(e))
        chain_value, norm = a.chain.value, a.norm
    start = to_db(runtime.parse_when(since, rt.clock(), name="--since")) if since else None
    rows = conn.execute(
        "SELECT seq, check_id, created_at, chain, address_norm, verdict, amount, client, "
        "record_hash FROM checks "
        "WHERE (?1 IS NULL OR (chain = ?1 AND address_norm = ?2)) "
        "AND (?3 IS NULL OR verdict = ?3) "
        "AND (?4 IS NULL OR client = ?4 COLLATE NOCASE) "
        "AND (?5 IS NULL OR created_at >= ?5) "
        "ORDER BY seq DESC LIMIT ?6",
        (chain_value, norm, verdict.value if verdict else None, client, start, limit),
    ).fetchall()
    records: list[dict[str, Any]] = [
        {
            "seq": r[0],
            "check_id": r[1],
            "created_at": r[2],
            "chain": r[3],
            "address": r[4],
            "verdict": r[5],
            "amount_usdt": r[6],
            "client": r[7],
            "record_hash": r[8],
        }
        for r in rows
    ]
    if as_json:
        typer.echo(json.dumps(records, indent=2))
        return
    if not records:
        typer.echo("no records")
    for rec in records:
        extra = f"  {rec['amount_usdt']} USDT" if rec["amount_usdt"] else ""
        who = f"  {rec['client']}" if rec["client"] else ""
        typer.echo(
            f"#{rec['seq']:<5} {rec['created_at']}  {rec['verdict']:<10} "
            f"{rec['chain']:<4} {rec['address']}{extra}{who}"
        )


@app.command()
def verify() -> None:
    """Recompute the hash chain; report the first broken record. Keep the head hash elsewhere."""
    rt = runtime.load()
    v = chain.verify(runtime.open_database(rt))
    if v.ok:
        typer.echo(f"audit log OK: {v.records} record(s)")
        typer.echo(f"head hash: {v.head_hash}")
        return
    typer.echo(
        f"error: audit log broken at record #{v.break_at}: {v.reason}. "
        f"{v.records} record(s) before it verify; last good hash {v.head_hash}",
        err=True,
    )
    raise typer.Exit(1)
