"""AT-38: random synthetic graphs → partition = 1 ± 0.001, reads ≤ max_nodes, identical JSON."""

import json
import tempfile
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from typing import Any

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from amlcheck.chain.base import Transfer
from amlcheck.config import Settings, Trace
from amlcheck.core.address import detect
from amlcheck.storage.db import open_db
from tests.unit.trace_world import Fake, T, addr, engine, sanction, tr

PEOPLE = [T] + [addr(f"p{i}") for i in range(14)]

edges = st.lists(
    st.tuples(
        st.integers(1, len(PEOPLE) - 1),  # sender (never the target itself as a sender here)
        st.integers(0, len(PEOPLE) - 1),  # recipient
        st.integers(1, 170),  # days ago
        st.integers(1, 50_000),  # amount
    ),
    min_size=1,
    max_size=60,
)


async def _run(
    xs: list[Transfer], sanctioned: list[int], max_nodes: int, branch: int, reverse: bool
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as d:
        conn = open_db(Path(d) / "a.db")
        try:
            fake = Fake(list(reversed(xs)) if reverse else xs)
            eng, _ = engine(
                conn, fake, Settings(trace=Trace(bsc_max_nodes=max_nodes, branch=branch))
            )
            if sanctioned:
                sanction(conn, *(PEOPLE[i] for i in sanctioned))
            t = await eng.run(detect(T))
            out: dict[str, Any] = t.to_json()
            out["budget"].pop("queries")
            out["budget"].pop("cache_hits")
            return out
        finally:
            conn.close()


@settings(max_examples=500, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    rows=edges,
    sanctioned=st.lists(st.integers(1, len(PEOPLE) - 1), max_size=3, unique=True),
    max_nodes=st.integers(1, 12),
    branch=st.integers(1, 5),
)
async def test_at38_random_graphs(
    rows: list[tuple[int, int, int, int]], sanctioned: list[int], max_nodes: int, branch: int
) -> None:
    xs = [
        tr(days, PEOPLE[s], PEOPLE[r], amount, n)
        for n, (s, r, days, amount) in enumerate(rows)
        if s != r
    ]
    first = await _run(xs, sanctioned, max_nodes, branch, reverse=False)
    if first["partition"]:
        total = sum((Decimal(v) for v in first["partition"].values()), Decimal(0))
        assert abs(total - 1) <= Decimal("0.001"), first["partition"]
        covered = sum(
            (Decimal(v) for k, v in first["partition"].items() if not k.startswith("untraced:")),
            Decimal(0),
        )
        assert abs(covered - Decimal(first["coverage"])) <= Decimal("0.000002")
    assert first["budget"]["nodes_read"] <= max(max_nodes, 1)
    # every path's addresses are connected by edges (methodology §7.8)
    pairs = {(e["from"], e["to"]) for e in first["edges"]}
    for p in first["paths"]:
        chain = p["addresses"]
        for parent, child in pairwise(chain):
            assert (child, parent) in pairs
    second = await _run(xs, sanctioned, max_nodes, branch, reverse=True)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
