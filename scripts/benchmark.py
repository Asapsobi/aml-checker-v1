"""The benchmark against MistTrack: import levels, record live, give reasons, report (P15, §14).

    uv run python scripts/benchmark.py import levels.csv [--date 2026-10-08]  # the owner's file
    uv run python scripts/benchmark.py record [--all]                         # live, traced checks
    uv run python scripts/benchmark.py reason <address> --cause gap --text "…"
    uv run python scripts/benchmark.py report [--k 6] [--decay 0.5] [--weight frozen=0.8] [--write]

The owner's file has the columns `address,chain,level,note` (`chain` and `note` may be empty).
Writes `tests/benchmark/benchmark.json`; `report --write` also writes `docs/benchmark.md`. Only the
owner's MistTrack level is kept per wallet (D-091): a score or any other column is ignored.
`record` runs against `AMLCHECK_HOME`: use a scratch copy, never the real data by accident.
"""

from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from amlcheck import benchmark as bm
from amlcheck.cli import check as check_cli
from amlcheck.cli import runtime
from amlcheck.config import Settings
from amlcheck.core.address import detect
from amlcheck.core.models import Chain
from amlcheck.core.risk import RISK
from amlcheck.report.check_json import check_json

ROOT = Path(__file__).resolve().parents[1]
SET = ROOT / "tests" / "benchmark" / "benchmark.json"
REPORT = ROOT / "docs" / "benchmark.md"


async def record_one(rt: runtime.Runtime, conn: sqlite3.Connection, w: bm.Wallet) -> bm.Wallet:
    """A live, traced check, as a large payment would get, read back from its audit record."""
    addr = detect(w.address, Chain(w.chain))
    result = await check_cli.run_screen(rt, conn, addr, None, "benchmark", "benchmark (§14)", True)
    data = check_json(conn, result.check_id)
    if data is None:  # pragma: no cover - the check was just recorded
        raise SystemExit(f"{w.address}: the check {result.check_id} wasn't recorded")
    return replace(
        w, recorded=bm.Recorded.from_check(data, result.tool_version, result.config_hash)
    )


def weights(items: list[str]) -> dict[str, Decimal]:
    out = {}
    for item in items:
        category, _, value = item.partition("=")
        if category not in RISK:
            raise SystemExit(f"--weight {item}: categories are {', '.join(sorted(RISK))}")
        try:
            out[category] = Decimal(value)
        except InvalidOperation:
            raise SystemExit(f"--weight {item}: the weight must be a number") from None
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    im = sub.add_parser("import")
    im.add_argument("file", type=Path)
    im.add_argument("--date", help="when the levels were looked up (default today, UTC)")
    rc = sub.add_parser("record")
    rc.add_argument("--all", action="store_true", help="re-record wallets already recorded")
    rs = sub.add_parser("reason")
    rs.add_argument("address")
    rs.add_argument("--cause", required=True, choices=sorted(bm.CAUSES))
    rs.add_argument("--text", required=True)
    rp = sub.add_parser("report")
    rp.add_argument("--k", type=Decimal)
    rp.add_argument("--decay", type=Decimal)
    rp.add_argument("--weight", action="append", default=[], metavar="CATEGORY=W")
    rp.add_argument("--write", action="store_true", help=f"also write {REPORT.relative_to(ROOT)}")
    args = p.parse_args()

    wallets = bm.load(SET)
    if args.cmd == "report":
        today = Settings().score
        current = bm.measure(wallets, k=today.k, decay=today.decay)
        override = args.k is not None or args.decay is not None or args.weight
        candidate = (
            bm.measure(
                wallets,
                k=args.k if args.k is not None else today.k,
                decay=args.decay if args.decay is not None else today.decay,
                weights=weights(args.weight),
            )
            if override
            else None
        )
        for w in bm.unreproduced(wallets):
            print(f"warning: {w.address}'s record doesn't give back its score", file=sys.stderr)
        text = bm.markdown(current, candidate)
        sys.stdout.write(text)
        if args.write:
            REPORT.write_text(text, encoding="utf-8")
        return 0
    if args.cmd == "import":
        date = args.date or datetime.now(UTC).date().isoformat()
        try:
            new, ignored = bm.read_levels(args.file, date)
        except ValueError as e:
            raise SystemExit(str(e)) from None
        if ignored:
            print(f"ignored columns (only the level is kept, D-091): {', '.join(ignored)}")
        wallets = bm.merge(wallets, new)
    elif args.cmd == "reason":
        hits = [w for w in wallets if w.address == args.address]
        if not hits:
            raise SystemExit(f"{args.address} is not in the benchmark")
        cause = bm.cause_of(args.cause)
        wallets = [
            replace(w, cause=cause, reason=args.text) if w.address == args.address else w
            for w in wallets
        ]
    else:
        rt = runtime.load()
        conn = runtime.open_database(rt)
        try:
            todo = [w for w in wallets if args.all or w.recorded is None]
            done = {w.key: w for w in wallets}
            for n, w in enumerate(todo, start=1):
                started = time.monotonic()
                new_w = asyncio.run(record_one(rt, conn, w))
                done[w.key] = new_w
                SET.parent.mkdir(parents=True, exist_ok=True)
                bm.dump(done.values(), SET)  # save as we go: a long run can be resumed
                r = new_w.recorded
                assert r is not None  # noqa: S101
                print(
                    f"[{n}/{len(todo)}] {w.chain} {w.address}: {r.verdict} · {r.score} "
                    f"{r.level} (MistTrack {w.expected}) ({time.monotonic() - started:.0f} s)",
                    flush=True,
                )
            wallets = list(done.values())
        finally:
            conn.close()
    SET.parent.mkdir(parents=True, exist_ok=True)
    bm.dump(wallets, SET)
    print(f"{len(wallets)} wallet(s) in {SET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
