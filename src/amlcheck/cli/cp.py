"""`amlcheck cp`: the counterparty registry (PRD F7.3) and its case report (F10.4)."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any

import typer

from amlcheck.cli import runtime
from amlcheck.core.address import AddressError, detect
from amlcheck.core.models import Chain, Verdict
from amlcheck.core.score import shown
from amlcheck.intel import registry
from amlcheck.intel.lookalike import lookalikes
from amlcheck.intel.store import IntelStore
from amlcheck.report import case_report

app = typer.Typer(
    help="Counterparties: every address checked, with its last verdict.", no_args_is_help=True
)


def _dict(cp: registry.Counterparty) -> dict[str, Any]:
    d = asdict(cp)
    d["chain"] = cp.chain.value
    d["clients"] = list(cp.clients)
    return d


@app.command("list")
def list_(
    chain: Annotated[Chain | None, typer.Option(help="Only this chain.")] = None,
    verdict: Annotated[Verdict | None, typer.Option(help="Only this last verdict.")] = None,
    client: Annotated[str | None, typer.Option(help="Only counterparties of this client.")] = None,
    limit: Annotated[int, typer.Option(min=1)] = 50,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Counterparties, most recently screened first."""
    rt = runtime.load()
    rows = registry.query(
        runtime.open_database(rt),
        chain=chain,
        verdict=verdict.value if verdict else None,
        client=client,
        limit=limit,
    )
    if as_json:
        typer.echo(json.dumps([_dict(r) for r in rows], indent=2))
        return
    for r in rows:
        who = f"  {', '.join(r.clients)}" if r.clients else ""
        typer.echo(
            f"{r.chain.value:<4} {r.address_norm:<42} {r.last_verdict:<10} "
            f"{shown(r.last_score, r.last_verdict):<15} "
            f"{r.check_count:>3}×  last {r.last_screened_at[:19]}Z{who}"
        )
    typer.echo(f"{len(rows)} counterpart{'y' if len(rows) == 1 else 'ies'}")


@app.command()
def show(
    address: Annotated[str, typer.Argument()],
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """One counterparty: registry row, labels, entity, look-alikes."""
    rt = runtime.load()
    try:
        addr = detect(address)
    except AddressError as e:
        runtime.fail(str(e))
    conn = runtime.open_database(rt)
    cp = registry.get(conn, addr.chain, addr.norm)
    store = IntelStore(conn, clock=rt.clock)
    entity = store.entity_for(addr.chain, addr.norm)
    terminal = store.best_terminal(addr.chain, addr.norm)
    info: dict[str, Any] = {
        "chain": addr.chain.value,
        "address": addr.norm,
        "registry": _dict(cp) if cp else None,
        "labels": [
            {"id": x.id, "category": x.category, "provenance": x.provenance, "note": x.note}
            for x in store.labels(addr.chain, addr.norm)
        ],
        "labels_csv": [
            r[0]
            for r in conn.execute(
                "SELECT tag FROM labels WHERE chain = ? AND address_norm = ? ORDER BY tag",
                (addr.chain.value, addr.norm),
            )
        ],
        "entity": {"id": entity.id, "name": entity.name, "kind": entity.kind, "role": entity.role}
        if entity
        else None,
        "best_category": terminal.category if terminal else None,
        "looks_like": lookalikes(conn, addr),
    }
    if as_json:
        typer.echo(json.dumps(info, indent=2))
        return
    echo = typer.echo
    echo(f"{addr.chain.value.upper()} {addr.norm}")
    if cp:
        echo(
            f"  checked {cp.check_count}× · first {cp.first_screened_at[:19]}Z · last "
            f"{cp.last_screened_at[:19]}Z → {cp.last_verdict}, score "
            f"{shown(cp.last_score, cp.last_verdict)} · "
            f"clients {', '.join(cp.clients) or '-'}"
        )
    else:
        echo("  not in the registry (never checked)")
    for x in info["labels"]:
        echo(
            f"  label #{x['id']}: {x['category']} ({x['provenance']})"
            f"{' · ' + x['note'] if x['note'] else ''}"
        )
    if info["labels_csv"]:
        echo(f"  labels.csv: {', '.join(info['labels_csv'])}")
    if entity:
        echo(f"  entity #{entity.id}: {entity.name} ({entity.kind}), role {entity.role}")
    for m in info["looks_like"]:
        echo(f"  LOOKS LIKE {m['address']} ({m['why_known']})")


@app.command()
def rebuild() -> None:
    """Recreate the registry from the audit log."""
    rt = runtime.load()
    conn = runtime.open_database(rt)
    n = registry.rebuild(conn)
    rows = len(registry.snapshot(conn))
    typer.echo(f"rebuilt from {n} audit record(s): {rows} counterpart{'y' if rows == 1 else 'ies'}")


@app.command()
def report(
    address: Annotated[str, typer.Argument(help="A counterparty that has been checked.")],
    check: Annotated[
        str | None, typer.Option("--check", help="This check instead of the latest.")
    ] = None,
    out: Annotated[Path | None, typer.Option(help="PDF file to write.")] = None,
) -> None:
    """Case report PDF: verdict, score, findings, sources, history, trace, classification."""
    rt = runtime.load()
    try:
        addr = detect(address)
    except AddressError as e:
        runtime.fail(str(e))
    conn = runtime.open_database(rt)
    try:
        data = case_report.gather(conn, addr, check)
    except case_report.ReportError as e:
        runtime.fail(str(e))
    finally:
        conn.close()
    day = data.record.created_at[:10]
    path = out or Path(f"case-{addr.chain.value}-{addr.norm[:10]}-{day}.pdf")
    try:
        path.write_bytes(case_report.render(data))
    except OSError as e:
        runtime.fail(f"could not write {path}: {e.strerror or e}")
    info = case_report.summary(data)
    score = f", score {info['score']}" if info["score"] else ""
    typer.echo(f"Case report written to {path}")
    typer.echo(f"  check {info['check_id']}: {info['verdict']}{score}")
    if not data.hash_ok:
        typer.echo("  WARNING: the record does not match its hash; run `amlcheck audit verify`")
