"""`amlcheck labels`: import labels.csv (whole-file replace, all-or-nothing) and list (PRD F5.3)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from amlcheck.cli import runtime
from amlcheck.intel.labels_csv import COLUMNS, LabelsError, parse, replace_all

app = typer.Typer(help="Your own address labels (labels.csv).", no_args_is_help=True)


@app.command("import")
def import_(
    path: Annotated[Path, typer.Argument(help=f"CSV with header {','.join(COLUMNS)}.")],
) -> None:
    """Replace all labels with the file's rows. One bad row and nothing changes."""
    rt = runtime.load()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        runtime.fail(f"can't read {path}: {e}")
    try:
        labels = parse(text)
    except LabelsError as e:
        for problem in e.problems:
            typer.echo(f"  {problem}", err=True)
        runtime.fail(f"{len(e.problems)} problem(s) in {path}; nothing imported")
    n = replace_all(runtime.open_database(rt), labels)
    tags = sorted({x.tag for x in labels})
    typer.echo(f"imported {n} label(s) from {path} (tags: {', '.join(tags) or 'none'})")


@app.command("list")
def list_(
    tag: Annotated[str | None, typer.Option(help="Only this tag.")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Labels currently in use."""
    rt = runtime.load()
    rows = (
        runtime.open_database(rt)
        .execute(
            "SELECT chain, address_norm, tag, note, source FROM labels "
            "WHERE (?1 IS NULL OR tag = ?1) ORDER BY chain, address_norm, tag",
            (tag.lower() if tag else None,),
        )
        .fetchall()
    )
    keys = ("chain", "address", "tag", "note", "source")
    items = [dict(zip(keys, r, strict=True)) for r in rows]
    if as_json:
        typer.echo(json.dumps(items, indent=2))
        return
    for r in rows:
        extra = f"  {r[3]}" if r[3] else ""
        typer.echo(f"{r[0]:<4} {r[1]:<42} {r[2]:<12}{extra}")
    typer.echo(f"{len(rows)} label(s)")
