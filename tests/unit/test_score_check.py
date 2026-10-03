import json
import sqlite3
from pathlib import Path

import pytest

from amlcheck.config import Settings
from amlcheck.core.address import detect
from amlcheck.core.audit import AuditRecord, append, verify
from amlcheck.core.clock import fixed
from amlcheck.core.engine import screen
from amlcheck.core.models import SourceStatus, Verdict
from amlcheck.core.score import from_json
from amlcheck.intel import registry
from amlcheck.storage.db import open_db
from amlcheck.trace.adapter import TraceSource
from amlcheck.trace.jobs import TraceJobs
from tests.unit.test_engine import Fake as Source
from tests.unit.trace_world import NOW, Fake, T, engine, example, setup_example


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / "a.db")


def traced(conn: sqlite3.Connection) -> TraceSource:
    eng, store = engine(conn, Fake(example()))
    setup_example(conn, store)
    return TraceSource(TraceJobs(conn, Settings(), clock=fixed(NOW)), eng, Settings())


# AT-42 through a check: the score is computed, stored (hashed) and put in the registry.
async def test_check_carries_its_score(conn: sqlite3.Connection) -> None:
    result = await screen(
        detect(T),
        [traced(conn), Source("exposure", rules=("R-HEU-01",))],
        conn=conn,
        settings=Settings(),
        now=fixed(NOW),
    )
    assert result.verdict is Verdict.REVIEW
    assert result.score is not None
    assert (result.score.score, result.score.band) == (66, "high")
    assert result.score.breakdown == "E 59.5 · D 0 · B 5 · U 1"
    stored = conn.execute(
        "SELECT score_json FROM checks WHERE check_id = ?", (result.check_id,)
    ).fetchone()[0]
    assert from_json(stored) == result.score
    assert json.loads(stored)["hazard"] == "0.24"
    cp = registry.get(conn, result.address.chain, result.address.norm)
    assert cp is not None
    assert cp.last_score == 66
    registry.rebuild(conn)
    again = registry.get(conn, result.address.chain, result.address.norm)
    assert again is not None
    assert again.last_score == 66  # rebuilt from the audit log's score_json


async def test_no_trace_scores_rules_only(conn: sqlite3.Connection) -> None:
    result = await screen(
        detect(T),
        [Source("ofac_sdn"), Source("exposure", rules=("R-EXP-01", "R-HEU-02"))],
        conn=conn,
        settings=Settings(),
        now=fixed(NOW),
    )
    assert result.score is not None
    assert result.score.shown == "35 · medium"  # D 25 + B 10
    assert result.score.hazard is None


async def test_block_and_incomplete_through_a_check(conn: sqlite3.Connection) -> None:
    block = await screen(
        detect(T),
        [Source("ofac_sdn", rules=("R-SAN-01",))],
        conn=conn,
        settings=Settings(),
        now=fixed(NOW),
    )
    assert block.score is not None
    assert block.score.shown == "100 · severe"
    gap = await screen(
        detect(T),
        [Source("ofac_sdn", status=SourceStatus.ERROR), Source("exposure", rules=("R-HEU-06",))],
        conn=conn,
        settings=Settings(),
        now=fixed(NOW),
    )
    assert gap.verdict is Verdict.INCOMPLETE
    assert gap.score is not None
    assert gap.score.shown == "≥ 20 · medium+"


# AT-44: records written before score_json existed and after it both verify; the score is hashed.
async def test_at44_old_and_new_records_verify(conn: sqlite3.Connection) -> None:
    old = AuditRecord(
        check_id="pre-p7",
        created_at="2026-10-01T10:00:00.000000Z",
        chain="bsc",
        address_norm=T,
        verdict="NO_HITS",
        tool_version="0.6.0",
        rules_version=1,
        config_hash="h",
    )
    assert "score_json" not in old.body()["check"]  # unset → not hashed (F6.4)
    append(conn, old)
    result = await screen(
        detect(T),
        [Source("exposure", rules=("R-HEU-01",))],
        conn=conn,
        settings=Settings(),
        now=fixed(NOW),
    )
    check = verify(conn)
    assert check.ok
    assert check.records == 2
    conn.execute(
        "UPDATE checks SET score_json = replace(score_json, '\"score\":5', '\"score\":0') "
        "WHERE check_id = ?",
        (result.check_id,),
    )
    broken = verify(conn)
    assert not broken.ok  # a changed score breaks the chain
    assert broken.break_at == 2
