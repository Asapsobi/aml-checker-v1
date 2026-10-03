"""`amlcheck classify`: who an address probably is, and why (PRD F8.5)."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from typing import Annotated, Any

import httpx
import typer

from amlcheck.chain.cache import ContractCache, TransferCache
from amlcheck.cli import runtime
from amlcheck.core.address import AddressError, detect
from amlcheck.core.models import Address
from amlcheck.intel.store import IntelStore
from amlcheck.net.http import Http, Mode, SourceError
from amlcheck.profile import classifier as clf
from amlcheck.profile.adapter import Profiler, Result


def classify(
    address: Annotated[str, typer.Argument(help="TRON (T…) or BSC (0x…) address.")],
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Profile and classify an address (types are inferences, never a verdict)."""
    rt = runtime.load()
    try:
        addr = detect(address)
    except AddressError as e:
        runtime.fail(str(e))
    conn = runtime.open_database(rt)
    try:
        r = asyncio.run(_run(rt, conn, addr))
    except SourceError as e:
        runtime.fail(str(e))
    store = IntelStore(conn, clock=rt.clock)
    entity = store.entity_for(addr.chain, addr.norm)
    terminal = store.best_terminal(addr.chain, addr.norm)
    data: dict[str, Any] = {
        "chain": addr.chain.value,
        "address": addr.norm,
        "classifier_version": clf.CLASSIFIER_VERSION,
        "types": [
            {
                "type": c.type,
                "confidence": str(c.confidence),
                "primary": c.primary,
                "conditions": list(c.conditions),
                "bonuses": list(c.bonuses),
            }
            for c in r.classifications
        ],
        "profile": clf.profile_json(r.profile),
        "entity": {"id": entity.id, "name": entity.name, "kind": entity.kind, "role": entity.role}
        if entity
        else None,
        "decisive_category": terminal.category if terminal else None,
    }
    if as_json:
        typer.echo(json.dumps(data, indent=2))
        return
    _print(addr, data)


async def _run(rt: runtime.Runtime, conn: sqlite3.Connection, addr: Address) -> Result:
    async with httpx.AsyncClient() as client:
        http = Http(client, rt.settings.network, mode=Mode.CHECK)
        cache = TransferCache(
            conn, runtime.make_sources(rt, client, Mode.CHECK), rt.settings.cache, clock=rt.clock
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
        return await profiler.classify(addr.chain, addr.norm)


def _print(addr: Address, d: dict[str, Any]) -> None:
    echo = typer.echo
    p = d["profile"]
    echo(f"{addr.chain.value.upper()} {addr.norm}  ·  classifier v{d['classifier_version']}")
    if not d["types"]:
        echo("  no type reached its confidence floor")
    for t in d["types"]:
        mark = "primary" if t["primary"] else ("tag" if t["type"] == "FRESH" else "also")
        echo(f"  {t['type']:<13} {t['confidence']:<5} {mark}")
        for c in t["conditions"]:
            echo(f"      ✓ {c}")
        for b in t["bonuses"]:
            echo(f"      + {b}")
    echo(
        f"Profile  {p['since'][:10]} → {p['until'][:10]}{'  (capped)' if p['capped'] else ''} · "
        f"in {p['n_in']} from {p['distinct_senders']} · out {p['n_out']} to "
        f"{p['distinct_recipients']} · {p['volume_in']} in / {p['volume_out']} out USDT"
    )
    echo(
        f"         median hold {p['median_hold_hours'] or '-'} h · pass-through 24 h "
        f"{p['pass_through_share_24h'] or '-'} · top recipient {p['top_recipient'] or '-'} "
        f"({p['top_recipient_share_out'] or '-'})"
    )
    if d["entity"]:
        e = d["entity"]
        echo(f"Entity   #{e['id']} {e['name']} ({e['kind']}), this address is the {e['role']}")
    if d["decisive_category"]:
        echo(f"Category {d['decisive_category']}")
    echo("Types are inferences from the address's own transfers: never a verdict on their own.")
