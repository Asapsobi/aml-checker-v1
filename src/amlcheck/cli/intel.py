"""`amlcheck intel`: operator labels, label packs, what is known about an address (PRD F7)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer

from amlcheck.cli import runtime
from amlcheck.core.address import AddressError, detect
from amlcheck.core.clock import to_iso
from amlcheck.intel.categories import CATEGORIES
from amlcheck.intel.packs import import_pack
from amlcheck.intel.store import IntelError, IntelStore, NewLabel

app = typer.Typer(help="Labels and what is known about addresses.", no_args_is_help=True)
_HUMAN_CATEGORIES = ", ".join(c.name for c in CATEGORIES if "operator" in c.provenances)


def _by(rt: runtime.Runtime, by: str | None) -> str | None:
    return by or rt.settings.operator.name or None


@app.command()
def label(
    address: Annotated[str, typer.Argument()],
    category: Annotated[str, typer.Argument(help=f"One of: {_HUMAN_CATEGORIES}.")],
    note: Annotated[str | None, typer.Option(help="Why.")] = None,
    by: Annotated[str | None, typer.Option(help="Who. Default: [operator] name.")] = None,
) -> None:
    """Label an address (operator provenance)."""
    rt = runtime.load()
    try:
        addr = detect(address)
        label_id = IntelStore(runtime.open_database(rt), clock=rt.clock).add_label(
            NewLabel(
                addr.chain,
                addr.norm,
                category.lower(),
                "operator",
                "operator",
                note=note,
                created_by=_by(rt, by),
            )
        )
    except (AddressError, IntelError) as e:
        runtime.fail(str(e))
    typer.echo(f"label #{label_id}: {addr.norm} is {category.lower()}")


@app.command()
def retract(
    label_id: Annotated[int, typer.Argument(help="Label number (see `intel show`).")],
    reason: Annotated[str, typer.Option(help="Why it is withdrawn.")],
    by: Annotated[str | None, typer.Option(help="Who. Default: [operator] name.")] = None,
) -> None:
    """Withdraw a label. It stays on record (shown with `intel show --all`)."""
    rt = runtime.load()
    try:
        IntelStore(runtime.open_database(rt), clock=rt.clock).retract(label_id, reason, _by(rt, by))
    except IntelError as e:
        runtime.fail(str(e))
    typer.echo(f"label #{label_id} retracted")


@app.command()
def show(
    address: Annotated[str, typer.Argument()],
    all_: Annotated[bool, typer.Option("--all", help="Include retracted labels.")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Labels on an address, its entity and its decisive category."""
    rt = runtime.load()
    try:
        addr = detect(address)
    except AddressError as e:
        runtime.fail(str(e))
    store = IntelStore(runtime.open_database(rt), clock=rt.clock)
    rows = store.labels(addr.chain, addr.norm, include_retracted=all_)
    terminal = store.best_terminal(addr.chain, addr.norm)
    entity = store.entity_for(addr.chain, addr.norm)
    data: dict[str, Any] = {
        "chain": addr.chain.value,
        "address": addr.norm,
        "labels": [
            {
                "id": x.id,
                "category": x.category,
                "provenance": x.provenance,
                "source_ref": x.source_ref,
                "confidence": str(x.confidence),
                "note": x.note,
                "licence": x.licence,
                "created_by": x.created_by,
                "created_at": to_iso(x.created_at),
                "retracted_at": to_iso(x.retracted_at) if x.retracted_at else None,
                "retracted_by": x.retracted_by,
                "retract_reason": x.retract_reason,
            }
            for x in rows
        ],
        "best_category": terminal.category if terminal else None,
        "entity": {"id": entity.id, "name": entity.name, "kind": entity.kind} if entity else None,
    }
    if as_json:
        typer.echo(json.dumps(data, indent=2))
        return
    typer.echo(
        f"{addr.chain.value.upper()} {addr.norm}  ·  "
        f"decisive category: {data['best_category'] or '-'}"
    )
    for x in data["labels"]:
        state = (
            f"RETRACTED {x['retracted_at']} ({x['retract_reason']})"
            if x["retracted_at"]
            else "active"
        )
        typer.echo(
            f"  #{x['id']:<4} {x['category']:<20} {x['provenance']:<9} {state}"
            f"{' · ' + x['note'] if x['note'] else ''}"
        )
    if not data["labels"]:
        typer.echo("  no labels" + ("" if all_ else " (add --all for retracted ones)"))


@app.command()
def stats(
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Counts of labels by provenance and category, entities, labels.csv rows."""
    rt = runtime.load()
    s = IntelStore(runtime.open_database(rt), clock=rt.clock).stats()
    if as_json:
        typer.echo(json.dumps(s, indent=2))
        return
    for prov, cats in s["active_labels"].items():
        typer.echo(f"{prov}: " + ", ".join(f"{c} {n}" for c, n in cats.items()))
    typer.echo(
        f"retracted {s['retracted_labels']} · entities {s['entities']} "
        f"({s['entity_members']} members) · labels.csv rows {s['labels_csv_rows']}"
    )


@app.command("import-pack")
def import_pack_(
    path: Annotated[Path, typer.Argument(help="CSV with header address,chain,category,note.")],
    name: Annotated[str, typer.Option(help="Pack name, e.g. acme-2026-10.")],
    licence: Annotated[
        str | None, typer.Option(help="The licence it is used under (required, PRD F7.4).")
    ] = None,
    by: Annotated[str | None, typer.Option(help="Who. Default: [operator] name.")] = None,
) -> None:
    """Import a licensed label pack. Record the licence check first (VS-14 template)."""
    rt = runtime.load()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        runtime.fail(f"can't read {path}: {e}")
    conn = runtime.open_database(rt)
    try:
        r = import_pack(
            IntelStore(conn, clock=rt.clock), conn, text, name=name, licence=licence, by=_by(rt, by)
        )
    except IntelError as e:
        runtime.fail(f"{e}; nothing imported")
    typer.echo(
        f"pack {name}: {r.added} label(s) imported, {r.retracted} earlier label(s) retracted"
    )
