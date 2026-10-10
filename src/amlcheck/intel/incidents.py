"""Public incidents that reveal a designated entity's wallets (methodology §13.5, D-101, VS-24).

On 2025-06-18 attackers with Nobitex's keys emptied Nobitex's TRON deposit addresses into a vanity
burn address, as public reports say (The Defiant, OODA Loop, SlowMist). On chain: 110,961 USDT
transfers from 110,626 addresses, USDT 49.4M, from 04:28:06 to 08:45:33 UTC, 99% in same-second
batches, and nothing else into it that week. The drain was scripted, many wallets per block; a
sender counts only when one of its transfers shares its block with `min_batch` - 1 others, which
leaves out lone outsiders (a busy service sent 0.5 USDT twice, each alone). Each sender left is
Nobitex's: an inference from a public event, never a list, never BLOCK. Nobitex kept
balances in its deposit addresses instead of sweeping them, so no other public link exists.

`sync` builds an incident's index once (the event is over) with our own TronGrid reads.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from amlcheck.chain.base import Transfer
from amlcheck.core.clock import Clock, to_db, utcnow
from amlcheck.core.models import Chain
from amlcheck.intel.designations import DESIGNATIONS, Designation
from amlcheck.storage.db import transaction


@dataclass(frozen=True)
class Incident:
    id: str
    entity: str  # a designation's entity (intel/designations.py)
    chain: Chain
    sink: str  # where the drained wallets sent their USDT
    since: datetime
    until: datetime
    what: str  # for people
    confidence: Decimal
    min_batch: int  # transfers into the sink in one block (one timestamp) for a sender to count

    @property
    def rule(self) -> str:
        """Stored with the index: a build under another rule is built again."""
        return f"window+batch>={self.min_batch}"

    @property
    def designation(self) -> Designation:
        return next(d for d in DESIGNATIONS if d.entity == self.entity)


INCIDENTS: tuple[Incident, ...] = (
    Incident(
        "nobitex_2025_06",
        "Nobitex",
        Chain.TRON,
        "TKFuckiRGCTerroristsNoBiTEXy2r7mNX",
        datetime(2025, 6, 18, 4, 0, tzinfo=UTC),
        datetime(2025, 6, 18, 9, 0, tzinfo=UTC),
        "drained in Nobitex's June 2025 hack",
        Decimal("0.9"),
        3,
    ),
)
BY_ID = {i.id: i for i in INCIDENTS}


@dataclass(frozen=True)
class Built:
    transfers: int
    addresses: int
    usdt: Decimal


def build(
    conn: sqlite3.Connection, incident: Incident, transfers: list[Transfer], clock: Clock = utcnow
) -> Built:
    """Store every sender into the sink within the window, in a batch; replaces an earlier build."""
    into = [
        t for t in transfers
        if t.recipient == incident.sink and incident.since <= t.time <= incident.until
    ]  # fmt: skip
    per_block = Counter(t.time for t in into)
    rows: dict[str, tuple[datetime, Decimal]] = {}
    total = Decimal(0)
    n = 0
    for t in into:
        if per_block[t.time] < incident.min_batch:
            continue
        n += 1
        total += t.amount
        first, amount = rows.get(t.sender, (t.time, Decimal(0)))
        rows[t.sender] = (min(first, t.time), amount + t.amount)
    with transaction(conn):
        conn.execute("DELETE FROM incident_addresses WHERE incident = ?", (incident.id,))
        conn.executemany(
            "INSERT INTO incident_addresses (incident, chain, address_norm, first_time, usdt) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (incident.id, incident.chain.value, a, to_db(first), str(amount))
                for a, (first, amount) in sorted(rows.items())
            ],
        )
        conn.execute(
            "INSERT OR REPLACE INTO incident_index "
            "(incident, built_at, rule, transfers, addresses, usdt) VALUES (?, ?, ?, ?, ?, ?)",
            (incident.id, to_db(clock()), incident.rule, n, len(rows), str(total)),
        )
    return Built(n, len(rows), total)


def built(conn: sqlite3.Connection, incident: Incident) -> Built | None:
    """The stored build, if it was made under the incident's current rule."""
    row = conn.execute(
        "SELECT transfers, addresses, usdt FROM incident_index WHERE incident = ? AND rule = ?",
        (incident.id, incident.rule),
    ).fetchone()
    return Built(row[0], row[1], Decimal(row[2])) if row else None


def incident_for(conn: sqlite3.Connection, chain: Chain, address: str) -> Incident | None:
    """The incident that reveals this address as a designated entity's, if any. Local only."""
    row = conn.execute(
        "SELECT incident FROM incident_addresses WHERE chain = ? AND address_norm = ? "
        "ORDER BY incident LIMIT 1",
        (chain.value, address),
    ).fetchone()
    return BY_ID.get(row[0]) if row else None
