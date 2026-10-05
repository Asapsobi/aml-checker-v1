"""`amlcheck lists`: sanctions lists imported by hand (methodology §13.1, D-085).

`lists import-nbctf FILES…`: the NBCTF seizure-order annexes the owner downloaded from the official
site (bot-protected, so never fetched by amlcheck). Every valid TRON and `0x` address in them is
listed; strings that look like TRON addresses but fail their checksum are reported, not imported.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from amlcheck.cli import runtime
from amlcheck.screening import lists

app = typer.Typer(help="Sanctions lists imported by hand (NBCTF).", no_args_is_help=True)


@app.command("import-nbctf")
def import_nbctf(
    files: Annotated[list[Path], typer.Argument(help="Order files: .xlsx, .ods, .csv or .txt.")],
    replace: Annotated[
        bool, typer.Option(help="Start over: keep only these files' addresses.")
    ] = False,
) -> None:
    """Import NBCTF seizure orders: their addresses BLOCK (D-085)."""
    rt = runtime.load()
    missing = [str(f) for f in files if not f.is_file()]
    if missing:
        runtime.fail(f"not a file: {', '.join(missing)}")
    conn = runtime.open_database(rt)
    try:
        try:
            done = lists.import_nbctf(conn, files, rt.clock(), replace=replace)
        except ValueError as e:
            runtime.fail(str(e))
    finally:
        conn.close()
    for f in done.files:
        bad = (
            f"; {len(f.rejected)} failed their checksum: {', '.join(f.rejected)}"
            if f.rejected
            else ""
        )
        typer.echo(f"{f.order}: {len(f.addresses)} address(es){bad}")
    typer.echo(f"NBCTF list: {done.total} address(es) in all; they BLOCK from now on")
    if not any(f.addresses for f in done.files):
        runtime.fail("no valid address in these files")
