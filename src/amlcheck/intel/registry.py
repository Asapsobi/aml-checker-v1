"""Counterparty registry: one row per checked address, derived from the audit log (PRD F7.3).

Upserted after every audit append. `rebuild()` replays the audit log from the start with the same
function, so it recreates exactly the same rows (AT-28).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from amlcheck.core.models import Chain
from amlcheck.storage.db import transaction


def lookalike_key(chain: Chain, address: str) -> str:
    """First 4 + last 4 characters of the body (methodology §4): TRON without the leading `T`,
    EVM lowercase hex without `0x`."""
    body = address[1:] if chain is Chain.TRON else address.lower().removeprefix("0x")
    return body[:4] + body[-4:]


@dataclass(frozen=True)
class CheckRow:
    check_id: str
    created_at: str
    chain: str
    address_norm: str
    verdict: str
    client: str | None
    score: int | None = None


def _apply(conn: sqlite3.Connection, c: CheckRow) -> None:
    row = conn.execute(
        "SELECT first_screened_at, clients_json, check_count FROM counterparties "
        "WHERE chain = ? AND address_norm = ?",
        (c.chain, c.address_norm),
    ).fetchone()
    clients = set(json.loads(row[1])) if row else set()
    if c.client:
        clients.add(c.client)
    conn.execute(
        "INSERT OR REPLACE INTO counterparties (chain, address_norm, lookalike_key, "
        "first_screened_at, last_screened_at, last_check_id, last_verdict, last_score, "
        "clients_json, check_count) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            c.chain,
            c.address_norm,
            lookalike_key(Chain(c.chain), c.address_norm),
            row[0] if row else c.created_at,
            c.created_at,
            c.check_id,
            c.verdict,
            c.score,
            json.dumps(sorted(clients)),
            (row[2] if row else 0) + 1,
        ),
    )


def upsert(conn: sqlite3.Connection, check: CheckRow) -> None:
    with transaction(conn):
        _apply(conn, check)


def rebuild(conn: sqlite3.Connection) -> int:
    """Recreate the registry from the audit log, oldest record first."""
    with transaction(conn):
        conn.execute("DELETE FROM counterparties")
        n = 0
        for r in conn.execute(
            "SELECT check_id, created_at, chain, address_norm, verdict, client, score_json "
            "FROM checks ORDER BY seq"
        ).fetchall():
            score = json.loads(r[6]).get("score") if r[6] else None
            _apply(conn, CheckRow(r[0], r[1], r[2], r[3], r[4], r[5], score))
            n += 1
    return n


@dataclass(frozen=True)
class Counterparty:
    chain: Chain
    address_norm: str
    lookalike_key: str
    first_screened_at: str
    last_screened_at: str
    last_check_id: str
    last_verdict: str
    last_score: int | None
    clients: tuple[str, ...]
    check_count: int


_COLS = (
    "chain, address_norm, lookalike_key, first_screened_at, last_screened_at, last_check_id, "
    "last_verdict, last_score, clients_json, check_count"
)


def _row(r: tuple[object, ...]) -> Counterparty:
    return Counterparty(
        Chain(str(r[0])),
        str(r[1]),
        str(r[2]),
        str(r[3]),
        str(r[4]),
        str(r[5]),
        str(r[6]),
        r[7] if isinstance(r[7], int) else None,
        tuple(json.loads(str(r[8]))),
        int(str(r[9])),
    )


def get(conn: sqlite3.Connection, chain: Chain, address: str) -> Counterparty | None:
    r = conn.execute(
        f"SELECT {_COLS} FROM counterparties WHERE chain = ? AND address_norm = ?",  # noqa: S608 - constant columns
        (chain.value, address),
    ).fetchone()
    return _row(r) if r else None


def query(
    conn: sqlite3.Connection,
    *,
    chain: Chain | None = None,
    verdict: str | None = None,
    client: str | None = None,
    limit: int = 50,
) -> list[Counterparty]:
    rows = conn.execute(
        f"SELECT {_COLS} FROM counterparties "  # noqa: S608 - constant columns
        "WHERE (?1 IS NULL OR chain = ?1) AND (?2 IS NULL OR last_verdict = ?2) "
        "AND (?3 IS NULL OR EXISTS (SELECT 1 FROM json_each(clients_json) "
        "WHERE lower(value) = lower(?3))) "
        "ORDER BY last_screened_at DESC, chain, address_norm LIMIT ?4",
        (chain.value if chain else None, verdict, client, limit),
    ).fetchall()
    return [_row(r) for r in rows]


def snapshot(conn: sqlite3.Connection) -> list[tuple[object, ...]]:
    """Every registry row, ordered: for comparing a rebuild with the live table."""
    return list(conn.execute(f"SELECT {_COLS} FROM counterparties ORDER BY chain, address_norm"))  # noqa: S608
