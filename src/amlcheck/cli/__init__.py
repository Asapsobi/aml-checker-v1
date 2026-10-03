"""Command line: one Typer sub-app per file (architecture §2).

Exit codes (D-019): 0 NO_HITS, 1 could not run, 3 REVIEW, 4 INCOMPLETE, 5 BLOCK, 6 needs attention.
"""

from __future__ import annotations

from importlib.metadata import version
from typing import Annotated

import typer

from amlcheck.cli import (
    audit,
    cache,
    check,
    classify,
    cp,
    history,
    intel,
    labels,
    status,
    sync,
    trace,
)

app = typer.Typer(
    name="amlcheck",
    help="Counterparty intelligence for USDT on TRON and BNB Smart Chain. Internal use only.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)
app.command("status")(status.status)
app.command("check")(check.check)
app.command("history")(history.history)
app.command("classify")(classify.classify)
app.command("trace")(trace.trace)
app.command("investigate")(trace.investigate)
app.command("sync")(sync.sync)
app.add_typer(audit.app, name="audit")
app.add_typer(labels.app, name="labels")
app.add_typer(cp.app, name="cp")
app.add_typer(intel.app, name="intel")
app.add_typer(cache.app, name="cache")


def _version(value: bool) -> None:
    if value:
        typer.echo(f"amlcheck {version('amlcheck')}")
        raise typer.Exit()


@app.callback()
def _root(
    show_version: Annotated[
        bool,
        typer.Option("--version", callback=_version, is_eager=True, help="Show the version."),
    ] = False,
) -> None:
    pass


def main() -> None:
    app()
