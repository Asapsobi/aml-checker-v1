"""Explorer tags (methodology §13.3, D-087, D-100): the public name tag Tronscan gives an address,
through Tronscan's own API with the owner's key (its terms forbid other automated means, VS-21).

Looked up only for the checked address, the busy wallets a trace stops at and a deposit address's
sweep target (§13.4), cached in `explorer_tags` for `[intel] tag_days`. A tag only names a service
the trace already found, so a failed lookup is no tag, never an error out of the trace: the wallet
stays `service_unattributed`, as before 2.1, and `failures` counts it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from datetime import timedelta
from typing import Protocol

from amlcheck.config import Intel
from amlcheck.core.clock import Clock, from_iso, to_db, utcnow
from amlcheck.core.models import Chain
from amlcheck.intel.designations import Designation, designation_for
from amlcheck.net.http import Http, Limiter, Provider, SourceError
from amlcheck.storage.db import transaction

#: After this many failed lookups a cache asks no more (one per check source and trace direction):
#: a provider that hangs costs each lookup its timeout and retries, out of the trace's time budget
#: (§13.3). The ones it then skips still count as failures, so the gap stays visible.
MAX_FAILURES = 2


class TagLookup(Protocol):
    source: str

    async def public_tag(self, address: str) -> str: ...


class TronscanTags:
    """`GET /api/account/tag?address=` → `publicTag` (VS-21, VS-23). An untagged address has no
    `publicTag` or an empty one: `''`."""

    source = "tronscan"

    def __init__(
        self, http: Http, settings: Intel, *, api_key: str, limiter: Limiter | None
    ) -> None:
        self._http = http
        self._url = settings.tronscan_url.rstrip("/") + "/api/account/tag"
        self._headers = {"TRON-PRO-API-KEY": api_key}
        self._provider = Provider("tronscan", limiter=limiter, refusal_statuses=frozenset({429}))

    async def public_tag(self, address: str) -> str:
        resp = await self._http.request(
            self._provider, "GET", self._url, headers=self._headers, params={"address": address}
        )
        try:
            data = resp.json()
        except ValueError:
            raise SourceError("tronscan", "answer is not JSON") from None
        if not isinstance(data, dict):
            raise SourceError("tronscan", "answer is not a JSON object")
        tag = data.get("publicTag")
        return tag.strip() if isinstance(tag, str) else ""


class TagCache:
    """Tags by chain, from the cache while fresh; a chain without a lookup has no tags."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        lookups: Mapping[Chain, TagLookup],
        settings: Intel,
        *,
        clock: Clock = utcnow,
    ) -> None:
        self._conn = conn
        self._lookups = lookups
        self._max_age = timedelta(days=settings.tag_days)
        self._clock = clock
        self.lookups = 0
        self.failures = 0
        self._failed: set[tuple[Chain, str]] = set()  # asked once per cache: no retry, no recount

    def cached(self, chain: Chain, address: str) -> tuple[str, str] | None:
        """(tag, fetched_at) as last stored, however old. Local data only."""
        row = self._conn.execute(
            "SELECT public_tag, fetched_at FROM explorer_tags WHERE chain = ? AND address_norm = ?",
            (chain.value, address),
        ).fetchone()
        return (row[0], row[1]) if row else None

    def provenance(self, chain: Chain, address: str) -> dict[str, str]:
        """Where and when the stored tag came from, for evidence: it may be up to `tag_days` old,
        or older after a failed refresh."""
        row = self._conn.execute(
            "SELECT source, fetched_at FROM explorer_tags WHERE chain = ? AND address_norm = ?",
            (chain.value, address),
        ).fetchone()
        return {"tag_source": row[0], "tag_fetched_at": row[1]} if row else {}

    async def public_tag(self, chain: Chain, address: str) -> str | None:
        """The tag (`''` when none); None when it can't be known (no lookup for the chain, or the
        lookup failed with nothing stored). A failed refresh keeps the stored tag."""
        stored = self.cached(chain, address)
        now = self._clock()
        if stored is not None and now - from_iso(stored[1]) < self._max_age:
            return stored[0]
        lookup = self._lookups.get(chain)
        if lookup is None or (chain, address) in self._failed:
            return stored[0] if stored else None
        if self.failures >= MAX_FAILURES:
            self.failures += 1  # skipped: a gap like a failure
            return stored[0] if stored else None
        self.lookups += 1
        try:
            tag = await lookup.public_tag(address)
        except SourceError:
            self.failures += 1
            self._failed.add((chain, address))
            return stored[0] if stored else None
        with transaction(self._conn):
            self._conn.execute(
                "INSERT OR REPLACE INTO explorer_tags (chain, address_norm, public_tag, source, "
                "fetched_at) VALUES (?, ?, ?, ?, ?)",
                (chain.value, address, tag, lookup.source, to_db(now)),
            )
        return tag

    async def designation(self, chain: Chain, address: str) -> Designation | None:
        """The designated entity whose wallet this is, by its public tag (§13.4)."""
        return designation_for(await self.public_tag(chain, address))


def stored_designation(conn: sqlite3.Connection, chain: Chain, address: str) -> Designation | None:
    """The same from the stored tag only, however old: for names, never a provider call (D-015)."""
    row = conn.execute(
        "SELECT public_tag FROM explorer_tags WHERE chain = ? AND address_norm = ?",
        (chain.value, address),
    ).fetchone()
    return designation_for(row[0]) if row else None
