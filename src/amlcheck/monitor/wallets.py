"""Own wallets (PRD F13.1, D-062).

An own wallet is labelled `own_or_trusted` by the operator (source `wallet:<name>`): it is never
screened as a sender, and the look-alike guard warns about addresses that imitate it. Removing a
wallet deactivates it and retracts that label; nothing is deleted.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from amlcheck.core.clock import to_db
from amlcheck.core.models import Address, Chain
from amlcheck.intel.store import IntelStore, NewLabel
from amlcheck.storage.db import transaction

CATEGORY = "own_or_trusted"


@dataclass(frozen=True)
class Wallet:
    chain: Chain
    address: str
    name: str
    added_at: str
    active: bool


def wallets(conn: sqlite3.Connection, *, active_only: bool = True) -> list[Wallet]:
    rows = conn.execute(
        "SELECT chain, address_norm, name, added_at, active FROM own_wallets "
        "WHERE (?1 = 0 OR active = 1) ORDER BY chain, name, address_norm",
        (int(active_only),),
    ).fetchall()
    return [Wallet(Chain(r[0]), r[1], r[2], r[3], bool(r[4])) for r in rows]


def is_own(conn: sqlite3.Connection, chain: Chain, address: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM own_wallets WHERE chain = ? AND address_norm = ? AND active = 1",
            (chain.value, address),
        ).fetchone()
        is not None
    )


def add(
    conn: sqlite3.Connection,
    store: IntelStore,
    address: Address,
    name: str,
    *,
    now: datetime,
    by: str | None,
) -> bool:
    """Register (or reactivate and rename) an own wallet; True when it is new."""
    name = name.strip()
    if not name:
        raise ValueError("an own wallet needs a name (--name)")
    with transaction(conn):
        known = conn.execute(
            "SELECT 1 FROM own_wallets WHERE chain = ? AND address_norm = ?",
            (address.chain.value, address.norm),
        ).fetchone()
        conn.execute(
            "INSERT INTO own_wallets (chain, address_norm, name, added_at, active) "
            "VALUES (?, ?, ?, ?, 1) ON CONFLICT (chain, address_norm) "
            "DO UPDATE SET name = excluded.name, active = 1",
            (address.chain.value, address.norm, name, to_db(now)),
        )
    labelled = any(
        x.category == CATEGORY and x.source_ref.startswith("wallet:")
        for x in store.labels(address.chain, address.norm)
    )
    if not labelled:
        store.add_label(
            NewLabel(
                address.chain,
                address.norm,
                CATEGORY,
                "operator",
                f"wallet:{name}",
                note="own wallet",
                created_by=by or "operator",
            )
        )
    return known is None


def remove(
    conn: sqlite3.Connection, store: IntelStore, address: Address, *, by: str | None
) -> bool:
    with transaction(conn):
        cur = conn.execute(
            "UPDATE own_wallets SET active = 0 WHERE chain = ? AND address_norm = ? AND active = 1",
            (address.chain.value, address.norm),
        )
    if cur.rowcount != 1:
        return False
    for x in store.labels(address.chain, address.norm):
        if x.category == CATEGORY and x.source_ref.startswith("wallet:"):
            store.retract(x.id, "no longer an own wallet", by)
    return True
