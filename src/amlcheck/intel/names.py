"""Who an address is, for people: names for exposures and for the checked address (methodology
§11.1, §11.6). Local data only, like every flag (D-015): never a provider call.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from amlcheck.core.models import Chain
from amlcheck.intel.store import IntelStore
from amlcheck.profile import classifier as clf
from amlcheck.screening.lists import NAMES, ORDER


def sanctions_entry(conn: sqlite3.Connection, address: str) -> str | None:
    """`OFAC SDN: <name>`, `UK sanctions: <name>`…, from each list's latest snapshot, OFAC first
    (methodology §13.1); the lowest entry id when one list names it several times."""
    rows = conn.execute(
        "SELECT s.source, a.entity_name, a.list_entry_id FROM sanctioned_addresses a "
        "JOIN list_snapshots s ON s.id = a.snapshot_id "
        "WHERE a.address_norm = ? AND a.snapshot_id IN "
        "(SELECT max(id) FROM list_snapshots GROUP BY source) "
        "ORDER BY s.source, a.list_entry_id",
        (address,),
    ).fetchall()
    if not rows:
        return None
    source, name, entry = min(
        rows, key=lambda r: (ORDER.index(r[0]) if r[0] in ORDER else len(ORDER), r[0], r[2])
    )
    list_name = NAMES.get(source, source)
    return f"{list_name}: {name}" if name else f"{list_name} entry {entry}"


def entity_name(conn: sqlite3.Connection, chain: Chain, address: str, category: str) -> str:
    """The name shown for a risk end of `category`."""
    if category == "sanctioned":
        return sanctions_entry(conn, address) or "sanctioned"
    if category == "frozen":
        return "Tether-frozen"
    entity = IntelStore(conn).entity_for(chain, address)
    if entity is not None and entity.named_by is not None:
        return entity.name
    return f"labelled {category}"


def _frozen_now(conn: sqlite3.Connection, chain: Chain, address: str) -> bool:
    """Tether's latest blacklist event for the address is an add (methodology §3.3)."""
    row = conn.execute(
        "SELECT event_type FROM issuer_events WHERE chain = ? AND address_norm = ? "
        "AND event_type IN ('AddedBlackList', 'RemovedBlackList') "
        "ORDER BY block DESC, event_index DESC LIMIT 1",
        (chain.value, address),
    ).fetchone()
    return row is not None and row[0] == "AddedBlackList"


def _label(
    name: str,
    source: str,
    category: str | None,
    confidence: str | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "source": source,
        "category": category,
        "inferred": confidence is not None,
        "confidence": confidence,
    }


def address_label(
    conn: sqlite3.Connection,
    chain: Chain,
    address: str,
    types: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any] | None:
    """Who the checked address is (methodology §11.6), the first that applies: the sanctions entry,
    Tether's freeze, an own wallet, a named entity, an intelligence label, the classifier's primary
    type (inferred). `types` is the classifier source's evidence. Says who it is; raises nothing."""
    entry = sanctions_entry(conn, address)
    if entry is not None:
        return _label(entry, "sanctions", "sanctioned")
    if _frozen_now(conn, chain, address):
        return _label("Tether-frozen", "freeze", "frozen")
    own = conn.execute(
        "SELECT name FROM own_wallets WHERE chain = ? AND address_norm = ? AND active = 1",
        (chain.value, address),
    ).fetchone()
    if own is not None:
        return _label(f"own wallet: {own[0]}", "own_wallet", "own_or_trusted")
    store = IntelStore(conn)
    entity = store.entity_for(chain, address)
    if entity is not None and entity.named_by is not None:
        return _label(entity.name, "entity", entity.kind if entity.kind != "unknown" else None)
    terminal = store.best_terminal(chain, address)
    if terminal is not None and terminal.provenance != "inferred":
        return _label(f"labelled {terminal.category}", "label", terminal.category)
    primary = next((t for t in types if t.get("primary")), None)
    if primary is not None:
        return _label(str(primary["type"]), "classifier", None, str(primary["confidence"]))
    return None


def label_text(label: Mapping[str, Any] | None) -> str | None:
    """`OFAC SDN: X`, `Binance (exchange_regulated)`, `PERSONAL (inferred, 0.5)`."""
    if not label:
        return None
    if label["inferred"]:
        return f"{label['name']} (inferred, {label['confidence']})"
    if label["source"] == "entity" and label["category"]:
        return f"{label['name']} ({label['category']})"
    return str(label["name"])


def known_as(conn: sqlite3.Connection, chain: Chain, address: str, now: datetime) -> str | None:
    """Who a counterparty is, for the counterparty table: its label as for a checked address, with
    the classifier's cached types (inferred) when nothing better is known. No provider call."""
    cached = clf.cached(conn, chain, address, now) or []
    types = [
        {"type": c.type, "confidence": str(c.confidence), "primary": c.primary} for c in cached
    ]
    return label_text(address_label(conn, chain, address, types))


def counterparty_text(c: Mapping[str, Any]) -> str:
    """The "known as" cell of a stored counterparty; records from before P12 have only flags."""
    if c.get("known_as"):
        return str(c["known_as"])
    return ", ".join(c["flags"]) or "-"
