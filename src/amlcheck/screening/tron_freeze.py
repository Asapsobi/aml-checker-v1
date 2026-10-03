"""Tether USDT freezes on TRON: local event index + live `isBlackListed` (PRD F4.1/F4.2, §2.3).

Index: `AddedBlackList`, `RemovedBlackList` and `DestroyedBlackFunds` of the USDT contract, read
from TronGrid (confirmed only, oldest first) up to the confirmed (solidity) head, and stored in
`issuer_events`. Each refresh first reads `deprecated()`: a retired contract makes the source
`error` (data sources §3, AT-18). A refresh that fails is not a gap until the index lags the chain
by more than `[freshness] tron_index_max_lag_minutes` (methodology §2.1, AT-13).

Rules for the target (methodology §2.3):
- latest blacklist event is `AddedBlackList`, or any `DestroyedBlackFunds` → R-FRZ-01 (BLOCK);
- `AddedBlackList` followed by `RemovedBlackList`, no later add → R-FRZ-02 (REVIEW);
- `isBlackListed` true (separate required source) → R-FRZ-01.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from amlcheck.chain.base import amount_from_units, canonical_amount
from amlcheck.chain.tron import USDT_DECIMALS, json_object, ms, suspension_seconds
from amlcheck.config import Freshness, Tron
from amlcheck.core.address import AddressError, tron_from_hex, tron_to_hex
from amlcheck.core.clock import Clock, from_iso, from_ms, to_db, to_iso, utcnow
from amlcheck.core.models import Address, Chain, Finding, SourceResult, SourceStatus
from amlcheck.core.rules import finding
from amlcheck.net.http import Http, Limiter, Provider, SourceError
from amlcheck.screening.base import SourceHealth
from amlcheck.storage.db import transaction

EVENTS = ("AddedBlackList", "RemovedBlackList", "DestroyedBlackFunds")
INDEX_SOURCE = "tron_freeze"
INDEX_LABEL = "Tether TRON freeze index"
SPOT_SOURCE = "tron_blacklist"
SPOT_LABEL = "Tether TRON isBlackListed"
PAGE_SIZE = 200


class ContractDeprecated(SourceError):
    """Tether retired the contract (`deprecated()` true): the index can't be trusted (AT-18)."""


def _state_key(event: str) -> str:
    return f"{INDEX_SOURCE}:{event}"


@dataclass(frozen=True)
class RefreshResult:
    new_events: int
    head_block: int
    head_time: datetime


class TronTether:
    """TronGrid calls for the USDT contract: confirmed head, constant calls, events."""

    def __init__(
        self, http: Http, settings: Tron, *, api_key: str | None, limiter: Limiter | None
    ) -> None:
        self._http = http
        self._api = settings.api_url.rstrip("/")
        self.contract = settings.usdt_contract
        self._headers = {"TRON-PRO-API-KEY": api_key} if api_key else {}
        self._provider = Provider(
            "trongrid",
            limiter=limiter,
            refusal_statuses=frozenset({429, 403}),
            refusal_wait=suspension_seconds,
        )

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        resp = await self._http.request(
            self._provider, "POST", self._api + path, headers=self._headers, json=body
        )
        return json_object(resp)

    async def head(self) -> tuple[int, datetime]:
        data = await self._post("/walletsolidity/getnowblock", {})
        try:
            raw = data["block_header"]["raw_data"]
            return int(raw["number"]), from_ms(int(raw["timestamp"]))
        except (KeyError, TypeError, ValueError):
            raise SourceError("trongrid", "confirmed head answer without block number") from None

    async def constant(self, selector: str, parameter: str = "") -> int:
        data = await self._post(
            "/wallet/triggerconstantcontract",
            {
                "owner_address": self.contract,
                "contract_address": self.contract,
                "function_selector": selector,
                "parameter": parameter,
                "visible": True,
            },
        )
        ok = data.get("result", {}).get("result")
        values = data.get("constant_result")
        if ok is not True or not isinstance(values, list) or not values:
            raise SourceError("trongrid", f"{selector} call failed")
        try:
            return int(values[0] or "0", 16)
        except ValueError:
            raise SourceError("trongrid", f"{selector} returned an unexpected value") from None

    async def events(self, name: str, since: datetime | None) -> list[dict[str, Any]]:
        url = f"{self._api}/v1/contracts/{self.contract}/events"
        params: dict[str, str | int] = {
            "event_name": name,
            "only_confirmed": "true",
            "order_by": "block_timestamp,asc",
            "limit": PAGE_SIZE,
        }
        if since is not None:
            params["min_block_timestamp"] = ms(since)
        out: list[dict[str, Any]] = []
        query: dict[str, str | int] | None = params
        seen: set[str] = set()
        while True:
            resp = await self._http.request(
                self._provider, "GET", url, headers=self._headers, params=query
            )
            data = json_object(resp)
            rows = data.get("data", [])
            if not isinstance(rows, list):
                raise SourceError("trongrid", "events answer without a data list")
            out.extend(rows)
            nxt = data.get("meta", {}).get("links", {}).get("next")
            if not nxt:
                return out
            if not isinstance(nxt, str) or not nxt.startswith(self._api + "/"):
                raise SourceError("trongrid", "next page link points elsewhere")
            if nxt in seen:
                raise SourceError("trongrid", "paging does not advance (a page link repeated)")
            seen.add(nxt)
            url, query = nxt, None


class TronFreezeIndex:
    def __init__(self, tether: TronTether, conn: sqlite3.Connection, *, clock: Clock = utcnow):
        self._tether = tether
        self._conn = conn
        self._clock = clock

    def last_synced(self) -> datetime | None:
        """The confirmed head time every event stream has been read up to (None: never)."""
        rows = self._conn.execute(
            "SELECT source, last_block_time FROM index_state WHERE source LIKE ?",
            (INDEX_SOURCE + ":%",),
        ).fetchall()
        if {r[0] for r in rows} != {_state_key(e) for e in EVENTS}:
            return None
        return min(from_iso(r[1]) for r in rows)

    async def refresh(self) -> RefreshResult:
        if await self._tether.constant("deprecated()"):
            raise ContractDeprecated(
                INDEX_SOURCE, "the Tether USDT contract reports deprecated(): index not trusted"
            )
        head_block, head_time = await self._tether.head()
        new = 0
        for name in EVENTS:
            state = self._conn.execute(
                "SELECT last_block_time FROM index_state WHERE source = ?", (_state_key(name),)
            ).fetchone()
            since = from_iso(state[0]) if state else None
            parsed = [self._event(name, r) for r in await self._tether.events(name, since)]
            # Newer than the head we record: left for the next refresh, which starts at that head.
            rows = [r for r in parsed if r is not None and r[8] <= to_db(head_time)]
            with transaction(self._conn):
                before = self._conn.total_changes
                self._conn.executemany(
                    "INSERT OR IGNORE INTO issuer_events (chain, token_contract, address_norm, "
                    "event_type, amount, tx_hash, event_index, block, block_time) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
                new += self._conn.total_changes - before
                self._conn.execute(
                    "INSERT OR REPLACE INTO index_state (source, last_block, last_block_time, "
                    "updated_at) VALUES (?, ?, ?, ?)",
                    (_state_key(name), head_block, to_db(head_time), to_db(self._clock())),
                )
        return RefreshResult(new, head_block, head_time)

    def _event(self, name: str, raw: Any) -> tuple[Any, ...] | None:
        try:
            if raw.get("event_name") != name:
                return None
            result = raw["result"]
            user_hex = result.get("_user") or result.get("_blackListedUser")
            amount = None
            if name == "DestroyedBlackFunds":
                amount = canonical_amount(amount_from_units(int(result["_balance"]), USDT_DECIMALS))
            return (
                Chain.TRON.value,
                self._tether.contract,
                tron_from_hex(str(user_hex)),
                name,
                amount,
                str(raw["transaction_id"]),
                int(raw["event_index"]),
                int(raw["block_number"]),
                to_db(from_ms(int(raw["block_timestamp"]))),
            )
        except (KeyError, TypeError, ValueError, AttributeError, AddressError):
            raise SourceError(INDEX_SOURCE, f"unexpected {name} event in answer") from None

    def events_for(self, address: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT event_type, amount, tx_hash, event_index, block, block_time FROM issuer_events "
            "WHERE chain = ? AND token_contract = ? AND address_norm = ? "
            "ORDER BY block, event_index",
            (Chain.TRON.value, self._tether.contract, address),
        ).fetchall()
        return [
            {
                "event": r[0],
                "amount_usdt": r[1],
                "tx_hash": r[2],
                "event_index": r[3],
                "block": r[4],
                "block_time": to_iso(from_iso(r[5])),
            }
            for r in rows
        ]


def freeze_findings(address: str, events: list[dict[str, Any]], now: datetime) -> list[Finding]:
    """Methodology §2.3 from the target's events, oldest first."""
    evidence_base = {"issuer": "Tether", "chain": "tron", "token": "USDT"}
    destroyed = [e for e in events if e["event"] == "DestroyedBlackFunds"]
    listing = [e for e in events if e["event"] in ("AddedBlackList", "RemovedBlackList")]
    latest = listing[-1] if listing else None
    if destroyed:
        d = destroyed[-1]
        return [
            finding(
                "R-FRZ-01",
                INDEX_SOURCE,
                f"Tether destroyed {d['amount_usdt']} USDT held here ({d['block_time']})",
                now,
                {**evidence_base, "event": d, "events": events},
            )
        ]
    if latest and latest["event"] == "AddedBlackList":
        return [
            finding(
                "R-FRZ-01",
                INDEX_SOURCE,
                f"Frozen by Tether on TRON since {latest['block_time']}",
                now,
                {**evidence_base, "event": latest, "events": events},
            )
        ]
    if latest and latest["event"] == "RemovedBlackList":
        return [
            finding(
                "R-FRZ-02",
                INDEX_SOURCE,
                f"Was frozen by Tether on TRON; released {latest['block_time']}",
                now,
                {**evidence_base, "event": latest, "events": events},
            )
        ]
    return []


class TronFreezeSource:
    source = INDEX_SOURCE
    label = INDEX_LABEL
    required = True
    timeout: float | None = None

    def __init__(self, index: TronFreezeIndex, freshness: Freshness, *, clock: Clock = utcnow):
        self._index = index
        self._max_lag = timedelta(minutes=freshness.tron_index_max_lag_minutes)
        self._clock = clock

    async def check(self, address: Address) -> SourceResult:
        now = self._clock()
        refresh_error: str | None = None
        try:
            await self._index.refresh()
        except ContractDeprecated as e:
            return self._result(now, SourceStatus.ERROR, e.reason)
        except SourceError as e:
            refresh_error = e.reason
        synced = self._index.last_synced()
        if synced is None:
            detail = "never synced" + (f" ({refresh_error})" if refresh_error else "")
            return self._result(now, SourceStatus.STALE, detail + "; run `amlcheck sync`")
        lag = now - synced
        minutes = lag.total_seconds() / 60
        events = self._index.events_for(address.norm)
        findings = freeze_findings(address.norm, events, now)
        detail = f"synced to the confirmed head of {to_iso(synced)} ({minutes:.0f} min behind now)"
        if refresh_error:
            detail += f"; refresh failed: {refresh_error}"
        status = SourceStatus.STALE if refresh_error and lag > self._max_lag else SourceStatus.OK
        return self._result(now, status, detail, tuple(findings), {"synced_to": to_iso(synced)})

    def _result(
        self,
        now: datetime,
        status: SourceStatus,
        detail: str,
        findings: tuple[Finding, ...] = (),
        evidence: dict[str, Any] | None = None,
    ) -> SourceResult:
        return SourceResult(
            self.source, self.label, self.required, status, now, findings, detail, evidence or {}
        )

    async def health(self) -> SourceHealth:
        synced = self._index.last_synced()
        now = self._clock()
        if synced is None:
            return SourceHealth(self.source, self.label, SourceStatus.STALE, None, "never synced")
        lag = now - synced
        status = SourceStatus.STALE if lag > self._max_lag else SourceStatus.OK
        return SourceHealth(
            self.source, self.label, status, synced, f"{lag.total_seconds() / 60:.0f} min behind"
        )


class TronBlacklistSource:
    """Live `isBlackListed(address)` for the target (PRD F4.2)."""

    source = SPOT_SOURCE
    label = SPOT_LABEL
    required = True
    timeout: float | None = None

    def __init__(self, tether: TronTether, *, clock: Clock = utcnow) -> None:
        self._tether = tether
        self._clock = clock

    async def check(self, address: Address) -> SourceResult:
        now = self._clock()
        try:
            listed = await self._tether.constant(
                "isBlackListed(address)", tron_to_hex(address.norm).rjust(64, "0")
            )
        except SourceError as e:
            return SourceResult(
                self.source, self.label, True, SourceStatus.ERROR, now, detail=e.reason
            )
        findings: tuple[Finding, ...] = ()
        if listed:
            findings = (
                finding(
                    "R-FRZ-01",
                    SPOT_SOURCE,
                    "Tether's USDT contract on TRON reports this address as blacklisted",
                    now,
                    {"issuer": "Tether", "chain": "tron", "token": "USDT", "call": "isBlackListed"},
                ),
            )
        return SourceResult(
            self.source,
            self.label,
            True,
            SourceStatus.OK,
            now,
            findings,
            f"isBlackListed = {'true' if listed else 'false'}",
        )

    async def health(self) -> SourceHealth:
        return SourceHealth(self.source, self.label, SourceStatus.OK, None, "live call per check")
