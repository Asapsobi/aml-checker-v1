"""An in-memory HyperSync for tests: oldest-first pages cut at block boundaries, hex timestamps.

Block times follow BSC's history in miniature: slow (3 s) early blocks, then 0.45 s.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

T0 = 1_700_000_000  # genesis timestamp (s)
USDT = "0x55d398326f99059ff775485246999027b3197955"
OTHER_TOKEN = "0x00000000000000000000000000000000000000aa"


def topic(addr: str) -> str:
    return "0x" + "0" * 24 + addr[2:]


@dataclass(frozen=True)
class Log:
    block: int
    index: int
    tx: str
    sender: str
    recipient: str
    units: int
    token: str = USDT


@dataclass(frozen=True)
class Tx:
    block: int
    sender: str
    recipient: str


@dataclass
class FakeHyperSync:
    head: int = 100_000
    slow_until: int = 10_000  # blocks before this are 3 s apart
    page_logs: int = 5  # a page stops after the block where this many logs are reached
    logs: list[Log] = field(default_factory=list)
    txs: list[Tx] = field(default_factory=list)
    queries: int = 0
    duplicate_self_transfers: bool = True

    def ts(self, block: int) -> int:
        if block <= self.slow_until:
            return T0 + 3 * block
        return T0 + 3 * self.slow_until + (9 * (block - self.slow_until)) // 20

    def time(self, block: int) -> datetime:
        return datetime.fromtimestamp(self.ts(block), UTC)

    def head_time(self) -> datetime:
        return self.time(self.head)

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/height":
            return httpx.Response(200, json={"height": self.head})
        self.queries += 1
        body = json.loads(request.content)
        lo, hi = body["from_block"], min(body["to_block"], self.head + 1)
        matched: list[tuple[int, dict[str, Any], str]] = []
        for sel in body.get("logs", []):
            addrs = sel.get("address")
            t = sel["topics"]
            for log in self.logs:
                if not lo <= log.block < hi or (addrs and log.token not in addrs):
                    continue
                if t[1] and topic(log.sender) not in t[1]:
                    continue
                if len(t) > 2 and t[2] and topic(log.recipient) not in t[2]:
                    continue
                matched.append((log.block, self._log_json(log), "logs"))
        for sel in body.get("transactions", []):
            for tx in self.txs:
                if not lo <= tx.block < hi:
                    continue
                if ("from" in sel and tx.sender in sel["from"]) or (
                    "to" in sel and tx.recipient in sel["to"]
                ):
                    matched.append((tx.block, {"block_number": tx.block}, "transactions"))
        if not self.duplicate_self_transfers:
            uniq: dict[str, tuple[int, dict[str, Any], str]] = {}
            for m in matched:
                uniq.setdefault(json.dumps(m[1], sort_keys=True), m)
            matched = list(uniq.values())
        matched.sort(key=lambda m: (m[0], m[1].get("log_index", -1)))
        out: list[tuple[int, dict[str, Any], str]] = []
        next_block = hi
        for i, m in enumerate(matched):
            out.append(m)
            if len(out) >= self.page_logs and (i + 1 == len(matched) or matched[i + 1][0] != m[0]):
                next_block = m[0] + 1
                break
        blocks = sorted({m[0] for m in out})
        batch = {
            "logs": [m[1] for m in out if m[2] == "logs"],
            "transactions": [m[1] for m in out if m[2] == "transactions"],
            "blocks": [{"number": b, "timestamp": hex(self.ts(b))} for b in blocks],
        }
        return httpx.Response(
            200,
            json={
                "data": [batch],
                "next_block": next_block,
                "archive_height": self.head + 1,
                "rollback_guard": None,
                "total_execution_time": 1,
            },
            headers={"x-ratelimit-remaining": "29000", "x-ratelimit-reset": "30"},
        )

    def _log_json(self, log: Log) -> dict[str, Any]:
        return {
            "block_number": log.block,
            "log_index": log.index,
            "transaction_hash": log.tx,
            "topic1": topic(log.sender),
            "topic2": topic(log.recipient),
            "data": "0x" + format(log.units, "064x"),
        }
