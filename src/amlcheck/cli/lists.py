"""`amlcheck lists`: sanctions lists imported by hand (methodology §13.1, D-085).

`lists import-nbctf FILES…`: the NBCTF seizure orders the owner downloaded, either the official
export (matal.mod.gov.il, one order per row, VS-20) or order files (one order per file). Every
valid TRON and `0x` address in them is listed; strings that look like TRON addresses but fail their
checksum are reported, not imported; cancelled orders are not listed.
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
    files: Annotated[
        list[Path],
        typer.Argument(
            help="The official export (.csv or .zip), or order files (.xlsx, .ods, .csv, .txt)."
        ),
    ],
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
    today = rt.clock().date()
    for f in done.files:
        if f.official:
            with_addresses = sum(1 for o in f.orders if o.addresses and not o.cancelled)
            typer.echo(
                f"{f.name}: {len(f.orders)} order(s) in the official export, "
                f"{with_addresses} with TRON or 0x addresses"
            )
        for o in f.orders:
            if f.official and not (o.addresses or o.rejected):
                continue  # bank accounts, phone numbers, other chains
            notes = [f"{len(o.addresses)} address(es)"]
            if o.cancelled:
                notes = ["cancelled; not listed"]
            elif o.lapsed(today):
                notes.append(f"validity date {o.valid_to} has passed; still listed (Q-38)")
            if o.rejected:
                notes.append(f"{len(o.rejected)} failed their checksum: {', '.join(o.rejected)}")
            typer.echo(("  " if f.official else "") + f"{o.order}: " + "; ".join(notes))
    listings = done.result.address_count
    several = (
        f" ({listings} listings: some are in several orders)" if listings != done.total else ""
    )
    typer.echo(f"NBCTF list: {done.total} address(es) in all{several}; they BLOCK from now on")
    if not any(o.addresses for f in done.files for o in f.orders):
        runtime.fail("no valid address in these files")
