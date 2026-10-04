"""`amlcheck batch`: screen a CSV of addresses (PRD F11.2, D-056).

- Input: a header row with `address` and any of `chain`, `amount`, `client`, `note`; nothing else.
- Every row is validated before any is screened: one bad row and nothing is screened (exit 1), with
  every problem listed by line (AT-45).
- Then one check at a time, like `check` (the trace runs at `[trace] auto_amount_usdt`), within the
  process's rate limits; each result is written as soon as it is known.
- Output CSV `row,address,chain,verdict,score,level,rules,check_id,record_hash`, guarded against
  spreadsheet formula injection. Exit: the most serious verdict (5 BLOCK, 4, 3, 0).
- Takes the run lock: never two batch or watch runs at once.
"""

from __future__ import annotations

import asyncio
import csv
import sqlite3
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated, TextIO

import typer

from amlcheck.cli import check as check_cli
from amlcheck.cli import runtime
from amlcheck.core.address import AddressError, detect
from amlcheck.core.models import Address, Chain, CheckResult, Verdict
from amlcheck.report.export import csv_cell
from amlcheck.storage.lock import Busy, run_lock

COLUMNS = ("address", "chain", "amount", "client", "note")
OUT_COLUMNS = (
    "row",
    "address",
    "chain",
    "verdict",
    "score",
    "level",  # methodology §11.4 (D-071); `band` until v2
    "rules",
    "check_id",
    "record_hash",
)
#: Exit codes in order of seriousness (D-019, D-056).
SERIOUS = (Verdict.BLOCK, Verdict.INCOMPLETE, Verdict.REVIEW, Verdict.NO_HITS)


@dataclass(frozen=True)
class Row:
    line: int  # line in the file (the header is line 1)
    address: Address
    amount: Decimal | None
    client: str | None
    note: str | None


def parse(text: str) -> tuple[list[Row], list[str]]:
    """All rows, or every problem found (then the batch must not run)."""
    reader = csv.DictReader(text.splitlines())
    header = [h.strip() for h in (reader.fieldnames or [])]
    if "address" not in header:
        return [], ["line 1: the header must name an `address` column"]
    unknown = [h for h in header if h not in COLUMNS]
    if unknown:
        allowed = ", ".join(COLUMNS)
        return [], [f"line 1: unknown column(s) {', '.join(unknown)}; allowed: {allowed}"]
    rows: list[Row] = []
    problems: list[str] = []
    for raw in reader:
        i = reader.line_num  # the csv module skips blank lines, so count from the file
        values = {k.strip(): (v or "").strip() for k, v in raw.items() if k is not None}
        if None in raw:
            problems.append(f"line {i}: more values than columns")
            continue
        if not any(values.values()):
            continue  # a blank line
        chain: Chain | None = None
        if values.get("chain"):
            try:
                chain = Chain(values["chain"].lower())
            except ValueError:
                problems.append(f"line {i}: chain must be tron or bsc, not {values['chain']!r}")
                continue
        try:
            address = detect(values.get("address", ""), chain)
        except AddressError as e:
            problems.append(f"line {i}: {e}")
            continue
        amount: Decimal | None = None
        if values.get("amount"):
            try:
                amount = Decimal(values["amount"].replace(",", ""))
            except InvalidOperation:
                problems.append(f"line {i}: amount is not a number: {values['amount']!r}")
                continue
            if not amount.is_finite() or amount < 0:
                problems.append(f"line {i}: amount must be a positive number")
                continue
        rows.append(
            Row(i, address, amount, values.get("client") or None, values.get("note") or None)
        )
    if not rows and not problems:
        problems.append("the file has no rows to screen")
    return rows, problems


def batch(
    file: Annotated[Path, typer.Argument(help="CSV with an `address` column.")],
    out: Annotated[
        Path | None, typer.Option(help="Write the results CSV here (default: standard output).")
    ] = None,
) -> None:
    """Screen every address in a CSV file, one at a time; results as CSV."""
    rt = runtime.load()
    try:
        text = file.read_text(encoding="utf-8-sig")
    except OSError as e:
        runtime.fail(f"could not read {file}: {e.strerror or e}")
    rows, problems = parse(text)
    if problems:
        for p in problems:
            typer.echo(f"error: {p}", err=True)
        runtime.fail(f"{len(problems)} problem(s) in {file}; nothing was screened")
    try:
        with run_lock(rt.paths.home, "batch"):
            conn = runtime.open_database(rt)
            try:
                if out is None:
                    worst = _run(rt, conn, rows, sys.stdout)
                else:
                    with out.open("w", newline="", encoding="utf-8") as f:
                        worst = _run(rt, conn, rows, f)
            finally:
                conn.close()
    except Busy as e:
        runtime.fail(str(e))
    raise typer.Exit(check_cli.EXIT[worst])


def _run(rt: runtime.Runtime, conn: sqlite3.Connection, rows: list[Row], sink: TextIO) -> Verdict:
    writer = csv.writer(sink, lineterminator="\n")
    writer.writerow(OUT_COLUMNS)
    sink.flush()
    seen: set[Verdict] = set()
    for n, row in enumerate(rows, start=1):
        trace = row.amount is not None and row.amount >= rt.settings.trace.auto_amount_usdt
        result = asyncio.run(
            check_cli.run_screen(rt, conn, row.address, row.amount, row.client, row.note, trace)
        )
        seen.add(result.verdict)
        writer.writerow([csv_cell(v) for v in _cells(row, result)])
        sink.flush()  # stream: each result as soon as it is known
        score = f" · {result.score.shown}" if result.score else ""
        typer.echo(
            f"[{n}/{len(rows)}] line {row.line}: {result.verdict.value}{score}  {row.address.norm}",
            err=True,
        )
    return next(v for v in SERIOUS if v in seen)


def _cells(row: Row, r: CheckResult) -> list[object]:
    return [
        row.line,
        r.address.norm,
        r.address.chain.value,
        r.verdict.value,
        r.score.score if r.score else "",
        (r.score.level + ("+" if r.score.lower_bound else "")) if r.score else "",
        " ".join(sorted({f.rule_id for f in r.findings})),
        r.check_id,
        r.record_hash,
    ]
