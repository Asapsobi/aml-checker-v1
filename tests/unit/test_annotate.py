from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from amlcheck.trace.annotate import layering
from amlcheck.trace.model import Node

D = Decimal
NOW = datetime(2026, 10, 1, tzinfo=UTC)


def chain(
    amounts: list[int], *, n_in: int = 1, share: str = "0.9", fresh: bool = True
) -> list[Node]:
    """Expanded nodes hop 1..k on one path T ← N1 ← N2 ← …, each forwarding `share` of inflow."""
    nodes = [Node("T", 0, D(1), None, True, None)]
    path: tuple[str, ...] = ("T",)
    weight = D(1)
    for i, sent in enumerate(amounts, start=1):
        address = f"N{i}"
        weight = weight * D("0.5")
        nodes.append(
            Node(
                address,
                i,
                weight,
                None,
                True,
                path[-1],
                11,
                path=path,
                n_in=n_in,
                inflow=D(sent) / D(share),
                first_seen=NOW,
                sent_on=D(sent),
                fresh=fresh,
            )
        )
        path = (*path, address)
    return nodes


def test_three_peeling_nodes_make_a_chain() -> None:
    # Money flows N3 → N2 → N1 → T: 1000, then 900, then 800 (never increasing).
    nodes = chain([800, 900, 1000])
    assert layering(nodes) == D("0.125")  # the outermost node's weight


@pytest.mark.parametrize(
    "nodes",
    [
        chain([800, 900]),  # only 2 nodes
        chain([1000, 900, 800]),  # amounts grow toward the target
        chain([800, 900, 1000], n_in=4),
        chain([800, 900, 1000], share="0.98"),  # forwards too much: a plain pass-through
        chain([800, 900, 1000], share="0.59"),
        chain([800, 900, 1000], fresh=False),
    ],
)
def test_not_a_chain(nodes: list[Node]) -> None:
    assert layering(nodes) == 0


def test_terminal_nodes_break_a_chain() -> None:
    nodes = chain([800, 900, 1000])
    nodes[2] = replace(nodes[2], terminal="untraced:depth")
    assert layering(nodes) == 0


def test_counted_once_per_segment() -> None:
    nodes = chain([800, 900, 1000, 1100])  # 4 qualifying nodes: one segment
    assert layering(nodes) == D("0.0625")
