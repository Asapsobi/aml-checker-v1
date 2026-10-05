"""Trace model and its JSON (methodology §7.8). Amounts and weights are `Decimal`, as strings.

Weights and shares stay exact while tracing and are rounded to 6 decimals only in JSON; the
partition still sums to 1 ± 0.001 (AT-38).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from amlcheck.chain.base import canonical_amount
from amlcheck.core.clock import from_iso, to_iso
from amlcheck.core.models import Chain

TRACE_VERSION = 2  # P13: best first, path-volume pruning, time → untraced:budget (§12.2)
_PLACES = Decimal("0.000001")

UNTRACED = (
    "untraced:cycle",
    "untraced:depth",
    "untraced:budget",
    "untraced:no_inflow",
    "untraced:pruned",
)


def dec(x: Decimal) -> str:
    """A weight or amount as JSON text: 6 decimals at most, no exponent, no trailing zeros."""
    return canonical_amount(x.quantize(_PLACES)) if x != x.to_integral() else canonical_amount(x)


def pct(x: Decimal) -> str:
    """A share for people: one decimal and a percent sign (CLI, graph, check detail)."""
    return f"{(x * 100).quantize(Decimal('0.1'))}%"


@dataclass(frozen=True)
class NodeClass:
    type: str
    confidence: Decimal


@dataclass(frozen=True)
class Node:
    address: str
    hop: int
    weight: Decimal
    terminal: str | None  # a category or untraced:<reason>; None when expanded
    read: bool
    via: str | None  # the address this item was reached from (None for the target)
    test: int | None = None  # which terminal test decided it (methodology §7.5)
    classification: NodeClass | None = None
    path: tuple[str, ...] = ()  # target first, up to `via`
    # Expanded nodes only, for the peel-chain annotation (methodology §6.1):
    n_in: int | None = None  # inbound transfers in its window
    inflow: Decimal | None = None  # USDT into it in its window
    first_seen: datetime | None = None
    sent_on: Decimal | None = None  # USDT it sent to `via` (its edge amount)
    fresh: bool | None = None  # first seen no earlier than 30 days before its edge (§6.1, D-048)
    # Terminal nodes: the smallest edge on its path, an absolute amount every hop moved (§7.3).
    bottleneck: Decimal | None = None


@dataclass(frozen=True)
class Edge:
    sender: str
    recipient: str
    amount: Decimal
    first: datetime
    last: datetime
    tx_sample: tuple[str, ...]


@dataclass(frozen=True)
class TracePath:
    to_category: str
    hops: int
    addresses: tuple[str, ...]  # target first
    bottleneck: Decimal
    estimated: Decimal
    terminal_test: int | None = None


@dataclass(frozen=True)
class Budget:
    nodes_read: int
    queries: int
    cache_hits: int
    seconds: float


@dataclass(frozen=True)
class Trace:
    chain: Chain
    target: str
    direction: str
    as_of: datetime
    target_inflow: Decimal  # USDT in the target's window (out-flow for direction "out")
    nodes: tuple[Node, ...]
    edges: tuple[Edge, ...]
    partition: dict[str, Decimal]
    annotations: dict[str, Decimal]
    coverage: Decimal | None
    paths: tuple[TracePath, ...]
    budget: Budget
    complete: bool = True  # False: a read failed or time ran out; this is the partial trace
    failure: str | None = None
    trace_version: int = TRACE_VERSION
    settings: dict[str, Any] = field(default_factory=dict)
    stopped: str | None = None  # "time": the budget ended it; the rest is untraced:budget (D-080)

    def to_json(self) -> dict[str, Any]:
        return {
            "trace_version": self.trace_version,
            "chain": self.chain.value,
            "target": self.target,
            "direction": self.direction,
            "as_of": to_iso(self.as_of),
            "target_inflow_usdt": dec(self.target_inflow),
            "nodes": [
                {
                    "address": n.address,
                    "hop": n.hop,
                    "weight": dec(n.weight),
                    "terminal": n.terminal,
                    "read": n.read,
                    "via": n.via,
                    "test": n.test,
                    "classification": (
                        {
                            "type": n.classification.type,
                            "confidence": dec(n.classification.confidence),
                        }
                        if n.classification
                        else None
                    ),
                    "path": list(n.path),
                    "n_in": n.n_in,
                    "inflow_usdt": dec(n.inflow) if n.inflow is not None else None,
                    "first_seen": to_iso(n.first_seen) if n.first_seen else None,
                    "sent_on_usdt": dec(n.sent_on) if n.sent_on is not None else None,
                    "fresh": n.fresh,
                    "bottleneck_usdt": dec(n.bottleneck) if n.bottleneck is not None else None,
                }
                for n in self.nodes
            ],
            "edges": [
                {
                    "from": e.sender,
                    "to": e.recipient,
                    "amount_usdt": dec(e.amount),
                    "first": to_iso(e.first),
                    "last": to_iso(e.last),
                    "tx_sample": list(e.tx_sample),
                }
                for e in self.edges
            ],
            "partition": {k: dec(v) for k, v in sorted(self.partition.items())},
            "annotations": {k: dec(v) for k, v in sorted(self.annotations.items())},
            "coverage": dec(self.coverage) if self.coverage is not None else None,
            "paths": [
                {
                    "to_category": p.to_category,
                    "hops": p.hops,
                    "addresses": list(p.addresses),
                    "bottleneck_usdt": dec(p.bottleneck),
                    "estimated_usdt": dec(p.estimated),
                    "terminal_test": p.terminal_test,
                }
                for p in self.paths
            ],
            "budget": {
                "nodes_read": self.budget.nodes_read,
                "queries": self.budget.queries,
                "cache_hits": self.budget.cache_hits,
                "seconds": round(self.budget.seconds, 1),
            },
            "complete": self.complete,
            "failure": self.failure,
            "settings": self.settings,
            "stopped": self.stopped,
        }

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Trace:
        return cls(
            chain=Chain(d["chain"]),
            target=d["target"],
            direction=d["direction"],
            as_of=from_iso(d["as_of"]),
            target_inflow=Decimal(d["target_inflow_usdt"]),
            nodes=tuple(
                Node(
                    n["address"],
                    n["hop"],
                    Decimal(n["weight"]),
                    n["terminal"],
                    n["read"],
                    n["via"],
                    n.get("test"),
                    NodeClass(
                        n["classification"]["type"], Decimal(n["classification"]["confidence"])
                    )
                    if n.get("classification")
                    else None,
                    tuple(n.get("path", ())),
                    n.get("n_in"),
                    Decimal(n["inflow_usdt"]) if n.get("inflow_usdt") is not None else None,
                    from_iso(n["first_seen"]) if n.get("first_seen") else None,
                    Decimal(n["sent_on_usdt"]) if n.get("sent_on_usdt") is not None else None,
                    n.get("fresh"),
                    Decimal(n["bottleneck_usdt"]) if n.get("bottleneck_usdt") is not None else None,
                )
                for n in d["nodes"]
            ),
            edges=tuple(
                Edge(
                    e["from"],
                    e["to"],
                    Decimal(e["amount_usdt"]),
                    from_iso(e["first"]),
                    from_iso(e["last"]),
                    tuple(e["tx_sample"]),
                )
                for e in d["edges"]
            ),
            partition={k: Decimal(v) for k, v in d["partition"].items()},
            annotations={k: Decimal(v) for k, v in d["annotations"].items()},
            coverage=Decimal(d["coverage"]) if d["coverage"] is not None else None,
            paths=tuple(
                TracePath(
                    p["to_category"],
                    p["hops"],
                    tuple(p["addresses"]),
                    Decimal(p["bottleneck_usdt"]),
                    Decimal(p["estimated_usdt"]),
                    p.get("terminal_test"),
                )
                for p in d["paths"]
            ),
            budget=Budget(**d["budget"]),
            complete=d["complete"],
            failure=d["failure"],
            trace_version=d["trace_version"],
            settings=d.get("settings", {}),
            stopped=d.get("stopped"),
        )
