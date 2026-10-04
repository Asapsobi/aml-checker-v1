"""`amlcheck case`: cases and decisions (PRD F12, D-058 to D-061).

The operator's name comes from `[operator] name`; `--by` overrides it. A decision without a name is
refused (D-058).
"""

from __future__ import annotations

import json
from importlib.metadata import version
from pathlib import Path
from typing import Annotated, Any

import typer

from amlcheck.cases import cases as store_cases
from amlcheck.cases import decisions, training
from amlcheck.cases.cases import Case, CaseError
from amlcheck.cli import runtime
from amlcheck.core.address import AddressError, detect
from amlcheck.core.models import Chain
from amlcheck.core.score import shown
from amlcheck.intel.store import IntelStore

app = typer.Typer(help="Cases: decide on REVIEW, BLOCK or INCOMPLETE checks.", no_args_is_help=True)

By = Annotated[str | None, typer.Option("--by", help="Who decides (default [operator] name).")]


def _name(rt: runtime.Runtime, by: str | None) -> str | None:
    return by or rt.settings.operator.name or None


def _case(conn: Any, case_id: str) -> Case:
    try:
        return store_cases.find(conn, case_id)
    except CaseError as e:
        runtime.fail(str(e))


@app.command("open")
def open_(
    address: Annotated[str, typer.Argument(help="TRON (T…) or BSC (0x…) address.")],
    check: Annotated[str | None, typer.Option(help="This check instead of the latest.")] = None,
    chain: Annotated[Chain | None, typer.Option(help="Chain for an 0x address.")] = None,
    by: By = None,
) -> None:
    """Open a case on an address's latest (or a chosen) REVIEW, BLOCK or INCOMPLETE check."""
    rt = runtime.load()
    try:
        addr = detect(address, chain)
    except AddressError as e:
        runtime.fail(str(e))
    conn = runtime.open_database(rt)
    try:
        case, new = store_cases.open_case(
            conn, addr, by=_name(rt, by), now=rt.clock(), check_id=check
        )
    except CaseError as e:
        runtime.fail(str(e))
    word = "opened" if new else "already open"
    typer.echo(f"case {case.case_id} {word} for {case.chain.value} {case.address}")
    typer.echo(f"  from check {case.opened_from}")


@app.command("list")
def list_(
    status: Annotated[str | None, typer.Option(help="open or closed.")] = None,
    address: Annotated[str | None, typer.Option(help="Only this address.")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Cases, newest first."""
    rt = runtime.load()
    addr = None
    if address:
        try:
            addr = detect(address)
        except AddressError as e:
            runtime.fail(str(e))
    conn = runtime.open_database(rt)
    rows = store_cases.cases(conn, status=status, address=addr)
    last = {c.case_id: decisions.stored(conn, case_id=c.case_id) for c in rows}
    if as_json:
        out = [
            {**_case_dict(c), "decisions": [_decision_dict(d) for d in last[c.case_id]]}
            for c in rows
        ]
        typer.echo(json.dumps(out, indent=2))
        return
    for c in rows:
        latest = last[c.case_id][-1].decision.decision if last[c.case_id] else "-"
        typer.echo(
            f"{c.case_id[:8]}  {c.status:<6}  {c.opened_at[:10]}  {latest:<9}  "
            f"{c.chain.value:<4} {c.address}  {c.client or ''}"
        )
    typer.echo(f"{len(rows)} case(s)")


def _case_dict(c: Case) -> dict[str, Any]:
    return {
        "case_id": c.case_id,
        "chain": c.chain.value,
        "address": c.address,
        "opened_from": c.opened_from,
        "opened_by": c.opened_by,
        "opened_at": c.opened_at,
        "status": c.status,
        "client": c.client,
    }


def _decision_dict(s: decisions.Stored) -> dict[str, Any]:
    return {**s.decision.body(), "seq": s.seq, "record_hash": s.record_hash}


@app.command()
def show(
    case_id: Annotated[str, typer.Argument(help="Case id (or its first 6+ characters).")],
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """A case: its check, decisions, inference feedback and the address's latest types."""
    rt = runtime.load()
    conn = runtime.open_database(rt)
    c = _case(conn, case_id)
    check = conn.execute(
        "SELECT verdict, score_json, created_at FROM checks WHERE check_id = ?", (c.opened_from,)
    ).fetchone()
    data = {
        **_case_dict(c),
        "check": {
            "check_id": c.opened_from,
            "verdict": check[0],
            "score": json.loads(check[1])["score"] if check[1] else None,
            "created_at": check[2],
        },
        "decisions": [_decision_dict(d) for d in decisions.stored(conn, case_id=c.case_id)],
        "feedback": [vars(f) for f in store_cases.feedback(conn, c.case_id)],
        "types": store_cases.latest_types(conn, c.chain, c.address),
    }
    if as_json:
        typer.echo(json.dumps(data, indent=2))
        return
    echo = typer.echo
    echo(f"case {c.case_id} · {c.status} · {c.chain.value} {c.address}")
    client = f" · client {c.client}" if c.client else ""
    echo(f"  opened {c.opened_at[:19]}Z by {c.opened_by}{client}")
    score = shown(data["check"]["score"], check[0])
    echo(f"  check {c.opened_from}: {check[0]} · score {score}")
    echo(f"  inferred types now: {', '.join(data['types']) or 'none'}")
    for d in data["decisions"]:
        when = d["created_at"][:19]
        echo(f"  #{d['seq']} {when}Z {d['decision'].upper()} by {d['operator']}: {d['note']}")
    for f in data["feedback"]:
        label = f" (label #{f['label_id']})" if f["label_id"] else ""
        version_ = f"classifier v{f['classifier_version']}"
        echo(f"  {f['verdict']} {f['type']} ({version_}){label} by {f['operator']}")
    if not data["decisions"]:
        echo("  no decision yet: amlcheck case decide <id> approved|rejected|escalated --note …")


@app.command()
def decide(
    case_id: Annotated[str, typer.Argument(help="Case id (or its first 6+ characters).")],
    decision: Annotated[str, typer.Argument(help="approved, rejected or escalated.")],
    note: Annotated[str, typer.Option(help="What was looked at and why.")] = "",
    check: Annotated[str | None, typer.Option(help="Decide on a newer check of it.")] = None,
    by: By = None,
) -> None:
    """Record a decision: approved / rejected close the case, escalated keeps it open."""
    rt = runtime.load()
    conn = runtime.open_database(rt)
    c = _case(conn, case_id)
    try:
        s = store_cases.decide(
            conn,
            c,
            decision.lower(),
            note,
            by=_name(rt, by),
            now=rt.clock(),
            tool_version=version("amlcheck"),
            check_id=check,
        )
    except CaseError as e:
        runtime.fail(str(e))
    after = store_cases.get(conn, c.case_id)
    typer.echo(
        f"{s.decision.decision} by {s.decision.operator} on check {s.decision.check_id}; "
        f"case {after.status if after else '?'}"
    )
    typer.echo(f"  decision #{s.seq}, hash {s.record_hash}")


@app.command()
def confirm(
    case_id: Annotated[str, typer.Argument(help="Case id (or its first 6+ characters).")],
    type_: Annotated[str, typer.Argument(metavar="TYPE", help="DEPOSIT, HUB, COLLECTOR, …")],
    category: Annotated[
        str | None, typer.Option(help="COLLECTOR/DISTRIBUTOR/PASS_THROUGH: the label's category.")
    ] = None,
    name: Annotated[str | None, typer.Option(help="HUB/DEPOSIT: name the entity.")] = None,
    kind: Annotated[str | None, typer.Option(help="With --name: the entity's kind.")] = None,
    by: By = None,
) -> None:
    """Confirm an inferred type of the case's address (D-059)."""
    rt = runtime.load()
    conn = runtime.open_database(rt)
    c = _case(conn, case_id)
    try:
        store_cases.confirm(
            conn,
            IntelStore(conn, clock=rt.clock),
            c,
            type_,
            by=_name(rt, by),
            now=rt.clock(),
            category=category,
            entity_name=name,
            kind=kind,
        )
    except CaseError as e:
        runtime.fail(str(e))
    typer.echo(f"confirmed {type_.upper()} for {c.address}")


@app.command()
def reject(
    case_id: Annotated[str, typer.Argument(help="Case id (or its first 6+ characters).")],
    type_: Annotated[str, typer.Argument(metavar="TYPE", help="DEPOSIT, HUB, COLLECTOR, …")],
    by: By = None,
) -> None:
    """Reject an inferred type: suppressed for the address until the classifier changes (D-059)."""
    rt = runtime.load()
    conn = runtime.open_database(rt)
    c = _case(conn, case_id)
    try:
        store_cases.reject(
            conn, IntelStore(conn, clock=rt.clock), c, type_, by=_name(rt, by), now=rt.clock()
        )
    except CaseError as e:
        runtime.fail(str(e))
    typer.echo(f"rejected {type_.upper()} for {c.address}: not assigned again by this classifier")


@app.command()
def export(
    jsonl: Annotated[bool, typer.Option("--jsonl", help="One JSON line per decision.")] = True,
    out: Annotated[Path | None, typer.Option(help="File to write (default: stdout).")] = None,
) -> None:
    """Every decision with the evidence it was made on, as JSON lines (D-061)."""
    rt = runtime.load()
    conn = runtime.open_database(rt)
    lines = training.lines(conn)
    text = "".join(json.dumps(x, sort_keys=True, ensure_ascii=False) + "\n" for x in lines)
    if out is None:
        typer.echo(text, nl=False)
        return
    try:
        out.write_text(text, encoding="utf-8")
    except OSError as e:
        runtime.fail(f"could not write {out}: {e.strerror or e}")
    typer.echo(f"{len(lines)} decision(s) exported to {out}", err=True)
