"""TronGrid: USDT (TRC20) transfers and first activity on TRON (data sources §6, VS-04, VS-10).

- Transfers: `/v1/accounts/{a}/transactions/trc20`, newest first, 200 per page via
  `meta.links.next`, `min_timestamp`/`max_timestamp` inclusive, confirmed only. Rows carry no block
  and no event index.
- Transfer identity (D-030): `idx` counts earlier rows of the same transaction with the identical
  (sender, recipient, amount), so the sender's and the recipient's histories give the same key.
- 0-value rows are dropped and counted (D-012).
- First activity: earliest of `getaccount.create_time` (absent on contract-created contracts) and
  the first USDT transfer (methodology §3.1).
- Refusals: 429 and 403 alike, no `Retry-After`; the key is suspended for N s (D-036).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from amlcheck.chain.base import History, Transfer, amount_from_units, newest_first
from amlcheck.config import Tron
from amlcheck.core.clock import Clock, ensure_utc, from_ms, utcnow
from amlcheck.core.models import Chain
from amlcheck.net.http import Http, Limiter, Provider, SourceError

PAGE_SIZE = 200
USDT_DECIMALS = 6
DEFAULT_SUSPENSION_S = 30.0
_SUSPENDED = re.compile(r"suspended for (\d+(?:\.\d+)?)\s*s")


def suspension_seconds(resp: httpx.Response) -> float:
    """Seconds in "…suspended for 30 s" (VS-05), else 30 (D-036)."""
    m = _SUSPENDED.search(resp.text)
    return float(m.group(1)) if m else DEFAULT_SUSPENSION_S


@dataclass(frozen=True)
class _Row:
    tx_hash: str
    time: datetime
    sender: str
    recipient: str
    units: int


class TronGridSource:
    chain = Chain.TRON

    def __init__(
        self,
        http: Http,
        settings: Tron,
        *,
        api_key: str | None,
        limiter: Limiter | None,
        clock: Clock = utcnow,
    ) -> None:
        self._http = http
        self._api = settings.api_url.rstrip("/")
        self._contract = settings.usdt_contract
        self._headers = {"TRON-PRO-API-KEY": api_key} if api_key else {}
        self._clock = clock
        self._provider = Provider(
            "trongrid",
            limiter=limiter,
            refusal_statuses=frozenset({429, 403}),
            refusal_wait=suspension_seconds,
        )

    async def fetch(
        self,
        address: str,
        since: datetime,
        until: datetime | None,
        limit: int,
        *,
        first_activity: bool,
    ) -> History:
        since = ensure_utc(since)
        until = ensure_utc(until) if until is not None else self._clock()
        rows, zero, more = await self._read(address, since, until, limit)
        transfers = newest_first(_with_idx(rows))
        first = await self._first_activity(address) if first_activity else None
        return History(
            transfers=transfers[:limit],
            since=since,
            until=until,
            complete=not more,
            first_activity=first,
            zero_value=zero,
        )

    async def _read(
        self, address: str, since: datetime, until: datetime, limit: int
    ) -> tuple[list[_Row], int, bool]:
        url = f"{self._api}/v1/accounts/{address}/transactions/trc20"
        params: Mapping[str, str | int] | None = {
            "contract_address": self._contract,
            "only_confirmed": "true",
            "limit": PAGE_SIZE,
            "order_by": "block_timestamp,desc",
            "min_timestamp": ms(since),
            "max_timestamp": ms(until),
        }
        rows: list[_Row] = []
        zero = 0
        seen: set[str] = set()
        while True:
            data = await self._get(url, params)
            for raw in _list(data, "data"):
                row = self._row(raw)
                if row is None or not since <= row.time <= until:
                    continue
                if row.units == 0:
                    zero += 1
                    continue
                rows.append(row)
            if len(rows) > limit:
                return rows, zero, True
            nxt = data.get("meta", {}).get("links", {}).get("next")
            if not nxt:
                return rows, zero, False
            if not isinstance(nxt, str) or not nxt.startswith(self._api + "/"):
                raise SourceError("trongrid", "next page link points elsewhere")
            if nxt in seen:
                raise SourceError("trongrid", "paging does not advance (a page link repeated)")
            seen.add(nxt)
            url, params = nxt, None

    def _row(self, raw: Any) -> _Row | None:
        try:
            if raw.get("type") != "Transfer":
                return None
            if raw["token_info"]["address"] != self._contract:
                return None
            return _Row(
                tx_hash=str(raw["transaction_id"]),
                time=from_ms(int(raw["block_timestamp"])),
                sender=str(raw["from"]),
                recipient=str(raw["to"]),
                units=int(raw["value"]),
            )
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            raise SourceError("trongrid", f"unexpected transfer row ({type(e).__name__})") from None

    async def _first_activity(self, address: str) -> datetime | None:
        resp = await self._http.request(
            self._provider,
            "POST",
            f"{self._api}/wallet/getaccount",
            headers=self._headers,
            json={"address": address, "visible": True},
        )
        account = json_object(resp)
        created = account.get("create_time")
        candidates = [from_ms(int(created))] if isinstance(created, int) else []
        data = await self._get(
            f"{self._api}/v1/accounts/{address}/transactions/trc20",
            {
                "contract_address": self._contract,
                "only_confirmed": "true",
                "limit": 1,
                "order_by": "block_timestamp,asc",
            },
        )
        first_rows = _list(data, "data")
        if first_rows:
            candidates.append(from_ms(int(first_rows[0]["block_timestamp"])))
        return min(candidates) if candidates else None

    async def _get(self, url: str, params: Mapping[str, str | int] | None) -> dict[str, Any]:
        resp = await self._http.request(
            self._provider, "GET", url, headers=self._headers, params=params
        )
        data = json_object(resp)
        if data.get("success") is False:
            raise SourceError("trongrid", f"answer not successful: {data.get('error', '?')}")
        return data


def _with_idx(rows: list[_Row]) -> list[Transfer]:
    """D-030: occurrence number of the identical (tx, sender, recipient, amount) row."""
    seen: Counter[tuple[str, str, str, int]] = Counter()
    out: list[Transfer] = []
    for r in rows:
        key = (r.tx_hash, r.sender, r.recipient, r.units)
        out.append(
            Transfer(
                chain=Chain.TRON,
                tx_hash=r.tx_hash,
                idx=seen[key],
                block=None,
                time=r.time,
                sender=r.sender,
                recipient=r.recipient,
                amount=amount_from_units(r.units, USDT_DECIMALS),
            )
        )
        seen[key] += 1
    return out


def json_object(resp: httpx.Response) -> dict[str, Any]:
    try:
        data = resp.json()
    except json.JSONDecodeError:
        raise SourceError("trongrid", "answer is not JSON") from None
    if not isinstance(data, dict):
        raise SourceError("trongrid", "answer is not a JSON object")
    return data


def _list(data: Mapping[str, Any], key: str) -> list[Any]:
    value = data.get(key, [])
    if not isinstance(value, list):
        raise SourceError("trongrid", f"'{key}' is not a list")
    return value


_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def ms(dt: datetime) -> int:
    """Exact milliseconds (floor), no float rounding at window edges."""
    return (dt - _EPOCH) // timedelta(milliseconds=1)


class TronContractLookup:
    """Contract detection via `getcontract`: `{}` for a wallet, `contract_address` for a contract
    (a contract created by a contract has no `bytecode`, so that is not the test; VS-13)."""

    chain = Chain.TRON

    def __init__(
        self, http: Http, settings: Tron, *, api_key: str | None, limiter: Limiter | None
    ) -> None:
        self._http = http
        self._api = settings.api_url.rstrip("/")
        self._headers = {"TRON-PRO-API-KEY": api_key} if api_key else {}
        self._provider = Provider(
            "trongrid",
            limiter=limiter,
            refusal_statuses=frozenset({429, 403}),
            refusal_wait=suspension_seconds,
        )

    async def is_contract(self, address: str) -> bool:
        resp = await self._http.request(
            self._provider,
            "POST",
            f"{self._api}/wallet/getcontract",
            headers=self._headers,
            json={"value": address, "visible": True},
        )
        return bool(json_object(resp).get("contract_address"))
