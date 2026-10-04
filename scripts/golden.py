"""The golden set: propose, add, record live, report (P11, methodology §10, D-066, D-067).

    uv run python scripts/golden.py propose-lists --per-chain 10      # BLOCK entries from our lists
    uv run python scripts/golden.py add --chain tron --address T… --type DEPOSIT --source owner
    uv run python scripts/golden.py import owner.csv                  # address,chain,kind,note
    uv run python scripts/golden.py record [--all]                    # live: check + classify
    uv run python scripts/golden.py report                            # offline replay, markdown

Writes `tests/golden/golden.json`. `record` runs live checks with the owner's keys against
`AMLCHECK_HOME` (use a scratch copy of the database, never the real one by accident); known-clean
addresses are checked with their trace, so their band is the one a large payment would get.
Expectations always carry their source; nothing here is imported as a label (D-066).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sqlite3
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path

import httpx

from amlcheck.calibration import Expect, Golden, Recorded, dump, load, markdown, measure
from amlcheck.chain.cache import ContractCache, TransferCache
from amlcheck.cli import check as check_cli
from amlcheck.cli import runtime
from amlcheck.config import Settings
from amlcheck.core.address import AddressError, detect
from amlcheck.core.clock import to_iso
from amlcheck.core.models import Chain
from amlcheck.intel.store import IntelStore
from amlcheck.net.http import Http, Mode
from amlcheck.profile import classifier as clf
from amlcheck.profile.adapter import Profiler

GOLDEN = Path(__file__).resolve().parents[1] / "tests" / "golden" / "golden.json"
KINDS = {
    "clean": Expect(clean=True),
    "deposit": Expect(type="DEPOSIT"),
    "hub": Expect(type="HUB"),
    "collector": Expect(type="COLLECTOR"),
    "personal": Expect(type="PERSONAL", clean=True),
    "block": Expect(verdict="BLOCK"),
    "review": Expect(verdict="REVIEW"),
}


def _merge(entries: list[Golden], new: list[Golden]) -> list[Golden]:
    by = {g.key: g for g in entries}
    for g in new:
        old = by.get(g.key)
        by[g.key] = replace(g, recorded=old.recorded) if old else g
    return list(by.values())


def propose_lists(conn: sqlite3.Connection, per_chain: int) -> list[Golden]:
    """BLOCK expectations from our own synced lists (D-066): OFAC (both chains), Tether (TRON)."""
    snap = conn.execute(
        "SELECT id, published_at FROM list_snapshots WHERE source = 'ofac_sdn' ORDER BY id DESC"
    ).fetchone()
    out: list[Golden] = []
    if snap is not None:
        rows = conn.execute(
            "SELECT DISTINCT address_norm, entity_name, program FROM sanctioned_addresses "
            "WHERE snapshot_id = ? ORDER BY address_norm",
            (snap[0],),
        ).fetchall()
        tron = [r for r in rows if r[0].startswith("T")][: per_chain // 2]
        evm = [r for r in rows if r[0].startswith("0x")][:per_chain]
        for chain, picked in (("tron", tron), ("bsc", evm)):
            for address, name, program in picked:
                out.append(
                    Golden(
                        chain,
                        address,
                        Expect(verdict="BLOCK"),
                        f"OFAC SDN list of {snap[1]}",
                        note=f"{name} ({program})",
                    )
                )
    frozen = conn.execute(
        "SELECT e.address_norm, max(e.block_time) FROM issuer_events e WHERE e.chain = 'tron' "
        "AND e.event_type = 'AddedBlackList' GROUP BY e.address_norm "
        "HAVING (SELECT x.event_type FROM issuer_events x WHERE x.chain = 'tron' "
        "AND x.address_norm = e.address_norm "
        "AND x.event_type IN ('AddedBlackList', 'RemovedBlackList') "
        "ORDER BY x.block DESC, x.event_index DESC LIMIT 1) = 'AddedBlackList' "
        "ORDER BY e.address_norm LIMIT ?",
        (per_chain - per_chain // 2,),
    ).fetchall()
    for address, when in frozen:
        out.append(
            Golden(
                "tron", address, Expect(verdict="BLOCK"), f"Tether TRON blacklist event of {when}"
            )
        )
    return out


def import_csv(path: Path) -> list[Golden]:
    out = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        for i, row in enumerate(csv.DictReader(f), start=2):
            kind = (row.get("kind") or "clean").strip().lower()
            if kind not in KINDS:
                raise SystemExit(f"line {i}: kind must be one of {', '.join(KINDS)}")
            chain = (row.get("chain") or "").strip().lower() or None
            try:
                a = detect(row["address"].strip(), Chain(chain) if chain else None)
            except (AddressError, ValueError) as e:
                raise SystemExit(f"line {i}: {e}") from None
            out.append(
                Golden(
                    a.chain.value,
                    a.norm,
                    KINDS[kind],
                    "owner",
                    note=(row.get("note") or "").strip() or None,
                )
            )
    return out


async def record_one(rt: runtime.Runtime, conn: sqlite3.Connection, g: Golden) -> Golden:
    addr = detect(g.address, Chain(g.chain))
    trace = g.expect.clean
    result = await check_cli.run_screen(rt, conn, addr, None, "golden", "golden set", trace)
    async with httpx.AsyncClient() as client:
        http = Http(client, rt.settings.network, mode=Mode.BACKGROUND)
        cache = TransferCache(
            conn,
            runtime.make_sources(rt, client, Mode.BACKGROUND, http=http),
            rt.settings.cache,
            clock=rt.clock,
        )
        profiler = Profiler(
            conn,
            cache,
            ContractCache(conn, runtime.make_contract_lookups(rt, http), clock=rt.clock),
            IntelStore(conn, clock=rt.clock),
            rt.settings.classifier,
            rt.settings.heuristics,
            rt.settings.trace,
            clock=rt.clock,
        )
        c = await profiler.classify(addr.chain, addr.norm, link=False)
    s = result.score
    return replace(
        g,
        recorded=Recorded(
            at=to_iso(rt.clock()),
            classifier_version=clf.CLASSIFIER_VERSION,
            profile=clf.profile_json(c.profile),
            context=asdict(c.context),
            types=[x.type for x in c.classifications],
            verdict=result.verdict.value,
            score=s.score if s else None,
            band=s.band if s else None,
            lower_bound=bool(s and s.lower_bound),
            traced=trace,
            check_id=result.check_id,
        ),
    )


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("propose-lists")
    pl.add_argument("--per-chain", type=int, default=10)
    ad = sub.add_parser("add")
    ad.add_argument("--chain", required=True, choices=["tron", "bsc"])
    ad.add_argument("--address", required=True)
    ad.add_argument("--kind", required=True, choices=sorted(KINDS))
    ad.add_argument("--source", required=True)
    ad.add_argument("--note")
    im = sub.add_parser("import")
    im.add_argument("file", type=Path)
    rc = sub.add_parser("record")
    rc.add_argument("--all", action="store_true", help="re-record entries already recorded")
    rc.add_argument("--chain", choices=["tron", "bsc"])
    sub.add_parser("report")
    args = p.parse_args()

    entries = load(GOLDEN)
    if args.cmd == "report":
        sys.stdout.write(markdown(measure(entries, Settings().classifier)))
        return 0
    if args.cmd == "add":
        a = detect(args.address, Chain(args.chain))
        entries = _merge(
            entries, [Golden(a.chain.value, a.norm, KINDS[args.kind], args.source, note=args.note)]
        )
    elif args.cmd == "import":
        entries = _merge(entries, import_csv(args.file))
    else:
        rt = runtime.load()
        conn = runtime.open_database(rt)
        try:
            if args.cmd == "propose-lists":
                entries = _merge(entries, propose_lists(conn, args.per_chain))
            else:
                todo = [
                    g
                    for g in entries
                    if (args.all or g.recorded is None)
                    and (args.chain is None or g.chain == args.chain)
                ]
                done = {g.key: g for g in entries}
                for n, g in enumerate(todo, start=1):
                    started = time.monotonic()
                    new = asyncio.run(record_one(rt, conn, g))
                    done[g.key] = new
                    dump(done.values(), GOLDEN)  # save as we go: a long run can be resumed
                    r = new.recorded
                    assert r is not None  # noqa: S101
                    took = time.monotonic() - started
                    print(
                        f"[{n}/{len(todo)}] {g.chain} {g.address}: {r.verdict} · "
                        f"{r.score} {r.band} · {','.join(r.types) or '-'} ({took:.0f} s)",
                        flush=True,
                    )
                entries = list(done.values())
        finally:
            conn.close()
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    dump(entries, GOLDEN)
    print(f"{len(entries)} golden entries in {GOLDEN.relative_to(Path.cwd())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
