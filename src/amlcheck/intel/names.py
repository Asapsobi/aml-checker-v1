"""Who an address is, for people: names for exposures and for the checked address (methodology
§11.1, §11.6). Local data only, like every flag (D-015): never a provider call.
"""

from __future__ import annotations

import sqlite3

from amlcheck.core.models import Chain
from amlcheck.intel.store import IntelStore


def sanctions_entry(conn: sqlite3.Connection, address: str) -> str | None:
    """`OFAC SDN: <name>` from the latest snapshot; the lowest entry id when several list it."""
    row = conn.execute(
        "SELECT a.entity_name, a.list_entry_id FROM sanctioned_addresses a "
        "WHERE a.address_norm = ? AND a.snapshot_id = (SELECT max(id) FROM list_snapshots "
        "WHERE source = 'ofac_sdn') ORDER BY a.list_entry_id LIMIT 1",
        (address,),
    ).fetchone()
    if row is None:
        return None
    name, entry = row
    return f"OFAC SDN: {name}" if name else f"OFAC SDN entry {entry}"


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
