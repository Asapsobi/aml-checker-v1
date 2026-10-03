"""Peel-chain annotation, `layering` (methodology §6.1).

On every trace path, a segment of ≥ 3 consecutive *expanded* nodes is a peel chain when every node
in it:
- has `n_in ≤ 3` inbound transfers in its window,
- was first seen within 30 days before its edge (D-048: from what the trace read or the cache
  already stores; no extra first-activity reads),
- sent ≥ 60% and < 98% of its window inflow on to the next node on the path,
- and edge amounts along the segment never increase in the direction money flowed.

The weight that passed through a segment (its outermost node's weight) is added to `layering`. It is
not part of the partition: the chain still ends somewhere, and that terminal keeps the share.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from amlcheck.trace.model import Node

MIN_SEGMENT = 3
MAX_IN = 3
LOW, HIGH = Decimal("0.6"), Decimal("0.98")


def _qualifies(n: Node) -> bool:
    if n.n_in is None or n.inflow is None or n.sent_on is None or not n.inflow:
        return False
    share = n.sent_on / n.inflow
    return n.n_in <= MAX_IN and bool(n.fresh) and LOW <= share < HIGH


def layering(nodes: Sequence[Node]) -> Decimal:
    expanded = {(n.path, n.address): n for n in nodes if n.terminal is None and n.hop > 0}
    counted: set[tuple[tuple[str, ...], str]] = set()
    total = Decimal(0)
    for key, node in sorted(expanded.items(), key=lambda kv: (-len(kv[0][0]), kv[0])):
        if key in counted:
            continue
        # The chain of expanded nodes from hop 1 out to this one, along its path.
        chain: list[Node] = []
        full = (*node.path, node.address)
        for i in range(1, len(full)):
            k = (full[:i], full[i])
            if k in expanded:
                chain.append(expanded[k])
        run: list[Node] = []
        for n in chain:  # inner (hop 1) → outer
            ok = _qualifies(n) and (
                not run
                or (
                    run[-1].sent_on is not None
                    and n.sent_on is not None
                    and n.sent_on >= run[-1].sent_on
                )  # outer edges ≥ inner: never increasing toward the target
            )
            if ok:
                run.append(n)
                continue
            total += _count(run, counted)
            run = [n] if _qualifies(n) else []
        total += _count(run, counted)
    return total


def _count(run: list[Node], counted: set[tuple[tuple[str, ...], str]]) -> Decimal:
    if len(run) < MIN_SEGMENT:
        return Decimal(0)
    outer = run[-1]
    key = (outer.path, outer.address)
    if key in counted:
        return Decimal(0)
    counted.update((n.path, n.address) for n in run)
    return outer.weight
