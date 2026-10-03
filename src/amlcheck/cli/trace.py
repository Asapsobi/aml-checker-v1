"""`amlcheck trace` and `amlcheck investigate` (PRD F9.4, U7).

- `trace` runs a source-of-funds trace as a stored job and shows where the money came from. It is an
  investigation, not a check: no verdict and no audit record. Exit 0 when complete, 4 when a read
  failed or time ran out (the partial trace is shown), 1 when it could not start.
- `investigate` is a check with the trace always on (D-049): one audit record, the check's verdict
  and exit codes (D-019), plus the full trace breakdown and an optional graph.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer

from amlcheck.cli import check as check_cli
from amlcheck.cli import runtime
from amlcheck.core.address import AddressError, detect
from amlcheck.core.models import Address, Chain
from amlcheck.net.http import Mode
from amlcheck.trace.engine import Direction, TraceFailed, TraceProgress
from amlcheck.trace.graph import render, short
from amlcheck.trace.jobs import TraceJobs
from amlcheck.trace.model import Trace, dec, pct
from amlcheck.trace.rules import trace_findings

EXIT_COMPLETE = 0
EXIT_INCOMPLETE = 4


def trace(
    address: Annotated[str, typer.Argument(help="TRON (T…) or BSC (0x…) address.")],
    chain: Annotated[
        Chain | None, typer.Option(help="Chain for an 0x address (only bsc in v1).")
    ] = None,
    direction: Annotated[
        str,
        typer.Option(help="in: where its money came from; out: where its money went."),
    ] = "in",
    svg: Annotated[Path | None, typer.Option(help="Also write the graph to this SVG file.")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Trace the source (or destination) of an address's USDT, up to 3 hops."""
    if direction not in ("in", "out"):
        runtime.fail(f"--direction must be in or out, not {direction!r}")
    rt = runtime.load()
    try:
        addr = detect(address, chain)
    except AddressError as e:
        runtime.fail(str(e))
    conn = runtime.open_database(rt)
    try:
        trace_id, result, reason = asyncio.run(
            _trace(rt, conn, addr, "out" if direction == "out" else "in")
        )
    finally:
        conn.close()
    if svg is not None:
        _write_svg(svg, result)
    if as_json:
        out = {"trace_id": trace_id, **result.to_json(), "findings": _findings(rt, result)}
        typer.echo(json.dumps(out, indent=2))
    else:
        print_trace(result, trace_id)
        findings = _findings(rt, result)
        if findings:
            typer.echo("")
            typer.echo("Trace findings  (no verdict: run `investigate` for a recorded check)")
            for f in findings:
                low = "  (low priority)" if f["evidence"].get("priority") == "low" else ""
                typer.echo(f"  {f['rule_id']:<9} {f['severity']:<10} {f['summary']}{low}")
        if reason:
            typer.echo("")
            typer.echo(f"INCOMPLETE: {reason}. The partial trace above is not a full picture.")
    if svg is not None:
        typer.echo(f"Graph written to {svg}", err=True)
    raise typer.Exit(EXIT_COMPLETE if reason is None else EXIT_INCOMPLETE)


async def _trace(
    rt: runtime.Runtime,
    conn: sqlite3.Connection,
    addr: Address,
    direction: Direction,
) -> tuple[str, Trace, str | None]:
    jobs = TraceJobs(conn, rt.settings, clock=rt.clock)
    trace_id = jobs.create(addr.chain, addr.norm, direction, requested_by="cli")
    async with httpx.AsyncClient() as client:
        engine = runtime.build_trace_engine(rt, conn, client, Mode.BACKGROUND)  # D-036
        try:
            result = await jobs.run(trace_id, engine, _progress if sys.stderr.isatty() else None)
        except TraceFailed as e:
            _progress_done()
            return trace_id, e.partial, e.reason
    _progress_done()
    return trace_id, result, None


def _progress(p: TraceProgress) -> None:
    sys.stderr.write(
        f"\r  hop {p.hop} · {p.items_done} done, {p.items_queued} queued · {p.nodes_read} read · "
        f"{p.queries} calls · {p.seconds:.0f} s   "
    )
    sys.stderr.flush()


def _progress_done() -> None:
    if sys.stderr.isatty():
        sys.stderr.write("\r" + " " * 78 + "\r")
        sys.stderr.flush()


def _findings(rt: runtime.Runtime, t: Trace) -> list[dict[str, Any]]:
    if not t.complete:
        return []  # a partial trace is a gap, not grounds for a finding (methodology §7.7)
    return [
        {
            "rule_id": f.rule_id,
            "severity": f.severity.value,
            "summary": f.summary,
            "evidence": f.evidence,
        }
        for f in trace_findings(t, rt.settings.trace, rt.clock())
    ]


def _write_svg(path: Path, t: Trace) -> None:
    try:
        path.write_text(render(t), encoding="utf-8")
    except OSError as e:
        runtime.fail(f"could not write {path}: {e.strerror or e}")


def print_trace(t: Trace, trace_id: str) -> None:
    """Exposure table, coverage, top paths and budget (T-6.10)."""
    echo = typer.echo
    what = "Source of funds" if t.direction == "in" else "Destination of funds"
    flow = "received" if t.direction == "in" else "sent"
    coverage = pct(t.coverage) if t.coverage is not None else "no inflow in the window"
    echo(f"{what}  ·  {t.chain.value.upper()} {t.target}")
    echo(f"{dec(t.target_inflow)} USDT {flow} in the window · coverage {coverage}")
    if t.partition:
        echo("")
        echo(f"  {'category':<24} {'share':>7}  {'≈ USDT':>14}")
        for category, share in sorted(t.partition.items(), key=lambda kv: (-kv[1], kv[0])):
            usdt = (share * t.target_inflow).quantize(Decimal("0.01"))
            echo(f"  {category:<24} {pct(share):>7}  {dec(usdt):>14}")
        layering = t.annotations.get("layering")
        if layering:
            echo(f"  {'layering (annotation)':<24} {pct(layering):>7}")
    if t.paths:
        echo("")
        echo("Top paths  (bottleneck = most that can have come this way; estimate = by share)")
        arrow = " ← " if t.direction == "in" else " → "
        for p in t.paths[:5]:
            chain_text = arrow.join(short(a) for a in p.addresses)
            echo(
                f"  {p.to_category:<22} {dec(p.bottleneck):>12} / {dec(p.estimated):<12} "
                f"{chain_text}"
            )
    b = t.budget
    echo("")
    echo(
        f"Budget  {b.nodes_read} address(es) read · {b.queries} provider call(s) · "
        f"{b.cache_hits} cache hit(s) · {b.seconds:.1f} s · trace {trace_id}"
    )
    echo("Shares are proportional estimates (D-016): USDT is fungible, so no amount is exact.")


def investigate(
    address: Annotated[str, typer.Argument(help="TRON (T…) or BSC (0x…) address.")],
    chain: Annotated[
        Chain | None, typer.Option(help="Chain for an 0x address (only bsc in v1).")
    ] = None,
    amount: Annotated[str | None, typer.Option(help="USDT amount of the payment.")] = None,
    client: Annotated[str | None, typer.Option(help="Who the check is for.")] = None,
    note: Annotated[str | None, typer.Option(help="Free text kept with the record.")] = None,
    svg: Annotated[Path | None, typer.Option(help="Also write the graph to this SVG file.")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Check an address with its source-of-funds trace: verdict, breakdown and graph."""
    rt = runtime.load()
    try:
        addr = detect(address, chain)
    except AddressError as e:
        runtime.fail(str(e))
    value = check_cli.parse_amount(amount)
    conn = runtime.open_database(rt)
    try:
        result = asyncio.run(check_cli.run_screen(rt, conn, addr, value, client, note, True))
        source = next((s for s in result.sources if s.source == "trace"), None)
        trace_id = str(source.evidence.get("trace_id")) if source and source.evidence else None
        job = TraceJobs(conn, rt.settings, clock=rt.clock).get(trace_id) if trace_id else None
    finally:
        conn.close()
    t = (job.result or job.partial) if job else None
    if svg is not None and t is not None:
        _write_svg(svg, t)
    if as_json:
        out = check_cli.as_dict(result)
        out["trace"] = {"trace_id": trace_id, **t.to_json()} if t is not None else None
        typer.echo(json.dumps(out, indent=2))
    else:
        check_cli.print_result(result)
        if t is not None and trace_id is not None:
            typer.echo("")
            print_trace(t, trace_id)
    if svg is not None and t is not None:
        typer.echo(f"Graph written to {svg}", err=True)
    raise typer.Exit(check_cli.EXIT[result.verdict])
