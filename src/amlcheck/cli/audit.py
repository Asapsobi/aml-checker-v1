"""`amlcheck audit`: list, verify and export the audit log (PRD F6.3, F6.5, F11)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, Literal

import typer

from amlcheck.cases import decisions
from amlcheck.cli import runtime
from amlcheck.core import audit as chain
from amlcheck.core.address import AddressError, detect
from amlcheck.core.clock import to_db
from amlcheck.core.models import Verdict
from amlcheck.core.score import shown_stored
from amlcheck.report import export as exporter

app = typer.Typer(help="List, verify and export the audit log.", no_args_is_help=True)


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
        "record_hash, score_json FROM checks "
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
            "score": json.loads(r[9])["score"] if r[9] else None,
            "score_shown": shown_stored(r[9]),
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
        score = rec["score_shown"]
        typer.echo(
            f"#{rec['seq']:<5} {rec['created_at']}  {rec['verdict']:<10} {score:<15} "
            f"{rec['chain']:<4} {rec['address']}{extra}{who}"
        )


@app.command()
def verify() -> None:
    """Recompute both hash chains (checks, decisions); report the first broken record of each.
    Keep the head hashes elsewhere."""
    rt = runtime.load()
    conn = runtime.open_database(rt)
    checks = chain.verify(conn)
    decided = decisions.verify(conn)
    broken = False
    for what, unit, v in (("audit log", "record", checks), ("decision log", "decision", decided)):
        if v.ok:
            typer.echo(f"{what} OK: {v.records} {unit}(s)")
            typer.echo(f"  head hash: {v.head_hash}")
            continue
        broken = True
        typer.echo(
            f"error: {what} broken at {unit} #{v.break_at}: {v.reason}. "
            f"{v.records} {unit}(s) before it verify; last good hash {v.head_hash}",
            err=True,
        )
    if broken:
        raise typer.Exit(1)


@app.command()
def export(
    fmt: Annotated[
        Literal["csv", "json", "pdf"], typer.Option("--format", help="csv, json or pdf.")
    ] = "csv",
    out: Annotated[Path | None, typer.Option(help="File to write (needed for pdf).")] = None,
    address: Annotated[str | None, typer.Option(help="Only this address.")] = None,
    verdict: Annotated[Verdict | None, typer.Option(help="Only this verdict.")] = None,
    client: Annotated[str | None, typer.Option(help="Only this client (any case).")] = None,
    since: Annotated[str | None, typer.Option(help="From: days back, or an ISO date/time.")] = None,
    until: Annotated[str | None, typer.Option(help="Before: days back or ISO date/time.")] = None,
) -> None:
    """Export recorded checks: CSV (spreadsheet-safe), JSON (with every hash) or a PDF table."""
    rt = runtime.load()
    chain_value = norm = None
    if address:
        try:
            a = detect(address)
        except AddressError as e:
            runtime.fail(str(e))
        chain_value, norm = a.chain.value, a.norm
    now = rt.clock()
    f = exporter.Filter(
        chain=chain_value,
        address=norm,
        verdict=verdict.value if verdict else None,
        client=client,
        since=to_db(runtime.parse_when(since, now, name="--since")) if since else None,
        until=to_db(runtime.parse_when(until, now, name="--until")) if until else None,
    )
    if fmt == "pdf" and out is None:
        runtime.fail("--format pdf needs --out FILE")
    conn = runtime.open_database(rt)
    try:
        found = exporter.rows(conn, f)
        data: str | bytes
        if fmt == "csv":
            data = exporter.to_csv(found)
        elif fmt == "json":
            data = exporter.to_json(conn, found)
        else:
            data = exporter.to_pdf(found, f)
    finally:
        conn.close()
    if out is None:  # csv or json: text (pdf needs --out, checked above)
        typer.echo(data if isinstance(data, str) else "", nl=False)
        return
    try:
        if isinstance(data, bytes):
            out.write_bytes(data)
        else:
            out.write_text(data, encoding="utf-8", newline="")
    except OSError as e:
        runtime.fail(f"could not write {out}: {e.strerror or e}")
    typer.echo(f"{len(found)} check(s) exported to {out}", err=True)
