import sqlite3
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from amlcheck.config import Trace as TraceSettings
from amlcheck.core.address import detect
from amlcheck.core.models import Severity
from amlcheck.storage.db import open_db
from amlcheck.trace.rules import trace_findings
from tests.unit.trace_world import NOW, A, Fake, T, engine, example, sanction, setup_example, tr

S = TraceSettings()


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / "a.db")


# AT-37 findings: R-TRC-01 (bottleneck 4,000), R-TRC-03, R-TRC-05.
async def test_at37_findings(conn: sqlite3.Connection) -> None:
    eng, store = engine(conn, Fake(example()))
    setup_example(conn, store)
    t = await eng.run(detect(T))
    found = {f.rule_id: f for f in trace_findings(t, S, NOW)}
    assert set(found) == {"R-TRC-01", "R-TRC-03", "R-TRC-05"}
    assert found["R-TRC-01"].evidence["paths"][0]["bottleneck_usdt"] == "4000"
    assert found["R-TRC-01"].severity is Severity.INFO  # v2: the score decides (D-072)
    assert found["R-TRC-03"].evidence["share"] == "0.2"
    assert found["R-TRC-05"].evidence["share"] == "0.1"
    assert found["R-TRC-05"].evidence["priority"] == "low"


async def test_hop1_sanctioned_is_exposure_territory(conn: sqlite3.Connection) -> None:
    eng, _ = engine(conn, Fake([tr(5, A, T, 1000)]))
    sanction(conn, A)
    t = await eng.run(detect(T))
    assert t.partition == {"sanctioned": Decimal(1)}
    assert trace_findings(t, S, NOW) == []  # R-EXP-01 covers a sanctioned direct sender


async def test_bottleneck_below_threshold_and_low_coverage(conn: sqlite3.Connection) -> None:
    eng, store = engine(conn, Fake(example()))
    setup_example(conn, store)
    t = await eng.run(detect(T))
    quiet = trace_findings(t, S.model_copy(update={"min_flagged_usdt": Decimal(4001)}), NOW)
    assert "R-TRC-01" not in {f.rule_id for f in quiet}
    low = replace(t, coverage=Decimal("0.4"))
    assert "R-TRC-04" in {f.rule_id for f in trace_findings(low, S, NOW)}


def test_no_findings_for_forward_or_empty_traces() -> None:
    from amlcheck.core.models import Chain
    from amlcheck.trace.model import Budget, Trace

    empty = Trace(Chain.BSC, T, "in", NOW, Decimal(0), (), (), {}, {}, None, (), Budget(1, 0, 0, 0))
    assert trace_findings(empty, S, NOW) == []
    forward = replace(empty, direction="out", coverage=Decimal(0))
    assert trace_findings(forward, S, NOW) == []
