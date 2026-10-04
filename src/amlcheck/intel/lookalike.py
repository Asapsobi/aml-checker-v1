"""Look-alike guard, R-HEU-06 (methodology §4, PRD F7.5).

Address poisoning: an attacker sends dust from an address that shares the first and last characters
of a real counterparty, hoping the operator copies it from history. The target is compared, by
look-alike key, with every registry counterparty and every trusted address (an `own_or_trusted`
intel label, a labels.csv `allowlist` tag, or an own wallet, D-062). Same key, different address
→ REVIEW, with both full addresses and the differing middle marked.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from amlcheck.core.clock import Clock, utcnow
from amlcheck.core.models import Address, Chain, Finding, SourceResult, SourceStatus
from amlcheck.core.rules import finding
from amlcheck.intel import registry
from amlcheck.intel.registry import lookalike_key
from amlcheck.screening.base import SourceHealth

SOURCE = "lookalike"
LABEL = "Look-alike guard"


def marked(address: str, other: str) -> str:
    """`address` with the part that differs from `other` in brackets: `TNHr[htVn…]XJJaa`."""
    p = 0
    while p < min(len(address), len(other)) and address[p] == other[p]:
        p += 1
    s = 0
    while s < min(len(address), len(other)) - p and address[-1 - s] == other[-1 - s]:
        s += 1
    return f"{address[:p]}[{address[p : len(address) - s]}]{address[len(address) - s :]}"


def _trusted(conn: sqlite3.Connection, chain: Chain) -> set[str]:
    rows = conn.execute(
        "SELECT address_norm FROM intel_labels WHERE chain = ?1 AND retracted_at IS NULL "
        "AND category = 'own_or_trusted' "
        "UNION SELECT address_norm FROM labels WHERE chain = ?1 AND tag = 'allowlist'",
        (chain.value,),
    )
    return {r[0] for r in rows}


def lookalikes(conn: sqlite3.Connection, address: Address) -> list[dict[str, Any]]:
    key = lookalike_key(address.chain, address.norm)
    known: dict[str, dict[str, Any]] = {}
    for cp in registry.query(conn, chain=address.chain, limit=1_000_000):
        if cp.lookalike_key == key and cp.address_norm != address.norm:
            known[cp.address_norm] = {
                "address": cp.address_norm,
                "why_known": "counterparty",
                "last_screened_at": cp.last_screened_at,
                "last_verdict": cp.last_verdict,
                "clients": list(cp.clients),
            }
    own = {
        r[0]: r[1]
        for r in conn.execute(
            "SELECT address_norm, name FROM own_wallets WHERE chain = ? AND active = 1",
            (address.chain.value,),
        )
    }
    for a, name in sorted(own.items()):
        if a != address.norm and lookalike_key(address.chain, a) == key:
            known[a] = {"address": a, "why_known": "own wallet", "wallet": name}
    for a in sorted(_trusted(conn, address.chain)):
        if a != address.norm and lookalike_key(address.chain, a) == key:
            known.setdefault(a, {"address": a, "why_known": "trusted"})
    return [known[a] for a in sorted(known)]


class LookalikeSource:
    source = SOURCE
    label = LABEL
    required = True
    timeout: float | None = None

    def __init__(self, conn: sqlite3.Connection, *, clock: Clock = utcnow) -> None:
        self._conn = conn
        self._clock = clock

    async def check(self, address: Address) -> SourceResult:
        now = self._clock()
        matches = lookalikes(self._conn, address)
        findings = tuple(self._finding(address, m, now) for m in matches)
        detail = (
            f"{len(matches)} known address(es) look like this one"
            if matches
            else "no known address looks like this one"
        )
        return SourceResult(SOURCE, LABEL, True, SourceStatus.OK, now, findings, detail)

    @staticmethod
    def _finding(address: Address, m: dict[str, Any], now: datetime) -> Finding:
        who = "a known counterparty" if m["why_known"] == "counterparty" else "a trusted address"
        when = f", last screened {m['last_screened_at']}" if m.get("last_screened_at") else ""
        return finding(
            "R-HEU-06",
            SOURCE,
            f"Looks like {who} but isn't: {marked(address.norm, m['address'])} vs "
            f"{marked(m['address'], address.norm)}{when}",
            now,
            {"target": address.norm, "looks_like": m},
        )

    async def health(self) -> SourceHealth:
        return SourceHealth(SOURCE, LABEL, SourceStatus.OK, None, "local registry and labels")
