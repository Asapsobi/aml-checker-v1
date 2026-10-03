"""The watchlist: addresses re-screened by `watch run` (PRD F11.3, D-057)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from amlcheck.core.clock import to_db
from amlcheck.core.models import Address, Chain
from amlcheck.storage.db import transaction


@dataclass(frozen=True)
class Watched:
    chain: Chain
    address: str
    client: str | None
    note: str | None
    added_at: str
    last_checked_at: str | None
    last_verdict: str | None
    last_check_id: str | None


def add(
    conn: sqlite3.Connection, address: Address, client: str | None, note: str | None, now: datetime
) -> bool:
    """Watch an address; True when it is new (a known one only gets its client and note updated)."""
    with transaction(conn):
        known = conn.execute(
            "SELECT 1 FROM watchlist WHERE chain = ? AND address_norm = ?",
            (address.chain.value, address.norm),
        ).fetchone()
        if known:
            conn.execute(
                "UPDATE watchlist SET client = coalesce(?, client), note = coalesce(?, note) "
                "WHERE chain = ? AND address_norm = ?",
                (client, note, address.chain.value, address.norm),
            )
            return False
        conn.execute(
            "INSERT INTO watchlist (chain, address_norm, client, note, added_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (address.chain.value, address.norm, client, note, to_db(now)),
        )
    return True


def remove(conn: sqlite3.Connection, address: Address) -> bool:
    with transaction(conn):
        cur = conn.execute(
            "DELETE FROM watchlist WHERE chain = ? AND address_norm = ?",
            (address.chain.value, address.norm),
        )
    return cur.rowcount == 1


def watched(conn: sqlite3.Connection) -> list[Watched]:
    rows = conn.execute(
        "SELECT chain, address_norm, client, note, added_at, last_checked_at, last_verdict, "
        "last_check_id FROM watchlist ORDER BY added_at, chain, address_norm"
    ).fetchall()
    return [Watched(Chain(r[0]), *r[1:]) for r in rows]


def record(
    conn: sqlite3.Connection, address: Address, verdict: str, check_id: str, at: datetime
) -> None:
    with transaction(conn):
        conn.execute(
            "UPDATE watchlist SET last_checked_at = ?, last_verdict = ?, last_check_id = ? "
            "WHERE chain = ? AND address_norm = ?",
            (to_db(at), verdict, check_id, address.chain.value, address.norm),
        )
