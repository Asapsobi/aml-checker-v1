"""Envio HyperSync: USDT (BEP20) transfers and first activity on BSC (data sources §7, VS-06/10).

HyperSync answers **oldest first** and stops a page near ~1,000 logs (continue from `next_block`).
To return the newest `limit` transfers of a window without reading all of a busy wallet's history:

1. Read the whole window once. A quiet wallet is done in one page (VS-06: 180 days, 1 query).
2. If the page didn't reach the window's end, use its log density to size chunks and read the window
   **backwards** in chunks from its newest end, each chunk to completion, until more than `limit`
   transfers are in hand or the window's start is reached.

Time → block: the window's first block is bounded from below with the *shortest* block time ever
(`[bsc] seconds_per_block`, 0.45 s): BSC block times only got shorter, so the estimate never starts
late. The last block is bounded from above with the longest (3 s, BSC's launch) or the head.
Exact times come from block timestamps (hex) and every transfer is filtered by time.

Budget: every `/query` costs the same; one pacer per token across both hosts (VS-06). The fallback
host is used only when the main one can't be reached.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from amlcheck.chain.base import History, Transfer, amount_from_units, newest_first
from amlcheck.config import Bsc
from amlcheck.core.clock import Clock, ensure_utc, utcnow
from amlcheck.core.models import Chain
from amlcheck.net.http import Http, Limiter, Provider, SourceError, Unreachable

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
USDT_DECIMALS = 18
LONGEST_BLOCK_S = 3.0  # BSC's original block time; an upper bound for any past interval
INDEX_LAG_S = 600.0  # how far the indexer's head may trail the clock, for the upper bound
MIN_CHUNK_BLOCKS = 2_000
CHUNK_HEADROOM = 1.5
_LOG_FIELDS = ["block_number", "log_index", "transaction_hash", "topic1", "topic2", "data"]


def reset_seconds(resp: httpx.Response) -> float | None:
    """HyperSync's refusal carries no Retry-After but `x-ratelimit-reset` (VS-06)."""
    value = resp.headers.get("x-ratelimit-reset")
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


@dataclass(frozen=True)
class _Log:
    block: int
    index: int
    tx_hash: str
    sender: str
    recipient: str
    units: int


@dataclass
class _Page:
    logs: list[_Log]
    next_block: int
    archive_height: int | None


class HyperSyncSource:
    chain = Chain.BSC

    def __init__(
        self,
        http: Http,
        settings: Bsc,
        *,
        token: str,
        pacer: Limiter | None,
        clock: Clock = utcnow,
    ) -> None:
        if not token:
            raise SourceError("hypersync", "no token (set AMLCHECK_HYPERSYNC_TOKEN)")
        self._http = http
        self._settings = settings
        self._hosts = [
            settings.hypersync_url.rstrip("/"),
            settings.hypersync_fallback_url.rstrip("/"),
        ]
        self._headers = {"Authorization": f"Bearer {token}"}
        self._clock = clock
        self._contract = settings.usdt_contract.lower()
        self._query = Provider(
            "hypersync", limiter=pacer, refusal_wait=reset_seconds, refusal_is_pacing=True
        )
        self._free = Provider("hypersync")  # /height costs no budget
        self._block_time: dict[int, datetime] = {}

    # --- public ---------------------------------------------------------------------------------

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
        addr = address.lower()
        head = await self._height()
        lo = self._lower_block(since, head)
        hi = self._upper_block(until, head)
        logs = await self._newest(addr, lo, hi, since, until, limit)
        nonzero, zero = [], 0
        for log in logs:
            when = self._block_time[log.block]
            if not since <= when <= until:
                continue
            if log.units == 0:
                zero += 1
                continue
            nonzero.append(
                Transfer(
                    chain=Chain.BSC,
                    tx_hash=log.tx_hash,
                    idx=log.index,
                    block=log.block,
                    time=when,
                    sender=log.sender,
                    recipient=log.recipient,
                    amount=amount_from_units(log.units, USDT_DECIMALS),
                )
            )
        ordered = newest_first(nonzero)
        first = await self._first_activity(addr, head) if first_activity else None
        return History(
            transfers=ordered[:limit],
            since=since,
            until=until,
            complete=len(ordered) <= limit,
            first_activity=first,
            zero_value=zero,
        )

    # --- reading ----------------------------------------------------------------------------------

    async def _newest(
        self, addr: str, lo: int, hi: int, since: datetime, until: datetime, limit: int
    ) -> list[_Log]:
        first = await self._page(addr, lo, hi)
        if first.next_block >= hi or _at_archive_end(first):
            return _dedupe(first.logs)  # the whole window in one page
        # Density over the blocks the page actually spanned with data: the window's estimated start
        # can lie long before the wallet's first transfer.
        start = min((log.block for log in first.logs), default=lo)
        density = max(len(first.logs), 1) / max(first.next_block - start, 1)
        got: list[_Log] = []
        cur_hi = hi
        while cur_hi > lo:
            in_window = sum(
                1 for g in got if g.units and since <= self._block_time[g.block] <= until
            )
            need = limit + 1 - in_window
            if need <= 0:
                break
            size = max(MIN_CHUNK_BLOCKS, math.ceil(need * CHUNK_HEADROOM / density))
            cur_lo = max(lo, cur_hi - size)
            chunk = await self._scan(addr, cur_lo, cur_hi)
            got = chunk + got
            if chunk:
                density = max(density, len(chunk) / (cur_hi - cur_lo))
            cur_hi = cur_lo
        return _dedupe(got)

    async def _scan(self, addr: str, lo: int, hi: int) -> list[_Log]:
        page = await self._page(addr, lo, hi)
        return await self._finish_page_chain(addr, page, hi)

    async def _finish_page_chain(self, addr: str, page: _Page, hi: int) -> list[_Log]:
        logs = list(page.logs)
        cur = page
        while cur.next_block < hi and not _at_archive_end(cur):
            cur = await self._page(addr, cur.next_block, hi)  # each page advances or raises
            logs.extend(cur.logs)
        return logs

    async def _page(self, addr: str, lo: int, hi: int) -> _Page:
        topic = _topic(addr)
        body = {
            "from_block": lo,
            "to_block": hi,
            "logs": [
                {"address": [self._contract], "topics": [[TRANSFER_TOPIC], [topic], []]},
                {"address": [self._contract], "topics": [[TRANSFER_TOPIC], [], [topic]]},
            ],
            "field_selection": {"block": ["number", "timestamp"], "log": _LOG_FIELDS},
        }
        data = await self._post(body)
        logs = [self._log(raw) for raw in _batches(data, "logs")]
        self._learn_blocks(_batches(data, "blocks"))
        missing = {log.block for log in logs} - self._block_time.keys()
        if missing:
            raise SourceError("hypersync", f"answer lacks timestamps for {len(missing)} block(s)")
        nb = data.get("next_block")
        if not isinstance(nb, int) or nb <= lo:
            raise SourceError("hypersync", "no progress (next_block missing or not advancing)")
        archive = data.get("archive_height")
        return _Page(logs, nb, archive if isinstance(archive, int) else None)

    async def _first_activity(self, addr: str, head: int) -> datetime | None:
        """First tx sent/received, or first token Transfer log naming the address (VS-10)."""
        topic = _topic(addr)
        cur = 0
        while cur < head:
            body = {
                "from_block": cur,
                "to_block": head + 1,
                "transactions": [{"from": [addr]}, {"to": [addr]}],
                "logs": [
                    {"topics": [[TRANSFER_TOPIC], [topic], []]},
                    {"topics": [[TRANSFER_TOPIC], [], [topic]]},
                ],
                "field_selection": {
                    "block": ["number", "timestamp"],
                    "transaction": ["block_number"],
                    "log": ["block_number"],
                },
            }
            data = await self._post(body)
            self._learn_blocks(_batches(data, "blocks"))
            hits = [
                int(x["block_number"])
                for key in ("transactions", "logs")
                for x in _batches(data, key)
            ]
            if hits:
                block = min(hits)
                if block not in self._block_time:
                    raise SourceError("hypersync", "answer lacks the first block's timestamp")
                return self._block_time[block]
            nb = data.get("next_block")
            if not isinstance(nb, int) or nb <= cur:
                raise SourceError("hypersync", "no progress while scanning for first activity")
            archive = data.get("archive_height")
            if isinstance(archive, int) and nb >= archive:
                return None
            cur = nb
        return None

    # --- transport --------------------------------------------------------------------------------

    async def _height(self) -> int:
        resp = await self._request(self._free, "GET", "/height", None)
        height = _json(resp).get("height")
        if not isinstance(height, int) or height <= 0:
            raise SourceError("hypersync", "height answer without a block number")
        return height

    async def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        return _json(await self._request(self._query, "POST", "/query", body))

    async def _request(
        self, provider: Provider, method: str, path: str, body: dict[str, Any] | None
    ) -> httpx.Response:
        last = Unreachable("hypersync", "no host configured")
        for host in self._hosts:
            try:
                return await self._http.request(
                    provider, method, host + path, headers=self._headers, json=body
                )
            except Unreachable as e:
                last = e
        raise last

    # --- block time -------------------------------------------------------------------------------

    def _learn_blocks(self, blocks: Sequence[Any]) -> None:
        for b in blocks:
            try:
                self._block_time[int(b["number"])] = _hex_time(b["timestamp"])
            except (KeyError, TypeError, ValueError):
                raise SourceError("hypersync", "unexpected block in answer") from None

    def _lower_block(self, when: datetime, head: int) -> int:
        """A block at or before `when`: never later than the true one."""
        seconds = max(0.0, (self._clock() - when).total_seconds())
        estimate = head - math.ceil(seconds / self._settings.seconds_per_block)
        known = [b for b, t in self._block_time.items() if t < when]
        return max(0, estimate, max(known, default=0))

    def _upper_block(self, when: datetime, head: int) -> int:
        """An exclusive end block past every block at or before `when`."""
        seconds = (self._clock() - when).total_seconds() - INDEX_LAG_S
        if seconds <= 0:
            return head + 1
        estimate = head - math.floor(seconds / LONGEST_BLOCK_S) + 1
        known = [b for b, t in self._block_time.items() if t > when]
        return max(1, min(head + 1, estimate, min(known, default=head + 1)))

    def _log(self, raw: Any) -> _Log:
        try:
            return _Log(
                block=int(raw["block_number"]),
                index=int(raw["log_index"]),
                tx_hash=str(raw["transaction_hash"]).lower(),
                sender=_address_from_topic(raw["topic1"]),
                recipient=_address_from_topic(raw["topic2"]),
                units=int(raw["data"], 16) if raw["data"] not in ("0x", "") else 0,
            )
        except (KeyError, TypeError, ValueError):
            raise SourceError("hypersync", "unexpected log in answer") from None


def _topic(addr: str) -> str:
    return "0x" + "0" * 24 + addr.removeprefix("0x")


def _address_from_topic(topic: str) -> str:
    if not isinstance(topic, str) or len(topic) != 66:
        raise ValueError("bad topic")
    return "0x" + topic[-40:].lower()


def _hex_time(value: str) -> datetime:
    return datetime.fromtimestamp(int(value, 16), UTC)


def _at_archive_end(page: _Page) -> bool:
    return page.archive_height is not None and page.next_block >= page.archive_height


def _dedupe(logs: list[_Log]) -> list[_Log]:
    # A self-transfer matches both log selections; one transfer per (tx, log index).
    seen: dict[tuple[str, int], _Log] = {}
    for log in logs:
        seen.setdefault((log.tx_hash, log.index), log)
    return list(seen.values())


def _batches(data: dict[str, Any], key: str) -> list[Any]:
    batches = data.get("data")
    if not isinstance(batches, list):
        raise SourceError("hypersync", "answer without data batches")
    out: list[Any] = []
    for batch in batches:
        items = batch.get(key, []) if isinstance(batch, dict) else None
        if not isinstance(items, list):
            raise SourceError("hypersync", f"'{key}' is not a list")
        out.extend(items)
    return out


def _json(resp: httpx.Response) -> dict[str, Any]:
    try:
        data = resp.json()
    except json.JSONDecodeError:
        raise SourceError("hypersync", "answer is not JSON") from None
    if not isinstance(data, dict):
        raise SourceError("hypersync", "answer is not a JSON object")
    return data


class BscContractLookup:
    """Contract detection via public JSON-RPC `eth_getCode` (no key, VS-12): `0x` means no code."""

    chain = Chain.BSC

    def __init__(self, http: Http, settings: Bsc, *, limiter: Limiter | None) -> None:
        self._http = http
        self._url = settings.rpc_url
        self._provider = Provider("bsc_rpc", limiter=limiter)

    async def is_contract(self, address: str) -> bool:
        resp = await self._http.request(
            self._provider,
            "POST",
            self._url,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "eth_getCode",
                "params": [address, "latest"],
            },
        )
        try:
            data = resp.json()
        except json.JSONDecodeError:
            raise SourceError("bsc_rpc", "answer is not JSON") from None
        if not isinstance(data, dict) or "error" in data or not isinstance(data.get("result"), str):
            raise SourceError("bsc_rpc", f"eth_getCode failed: {str(data)[:120]}")
        code = data["result"].lower()
        return code not in ("0x", "0x0", "")
