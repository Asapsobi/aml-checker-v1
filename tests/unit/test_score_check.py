import json
import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from amlcheck.config import Rules, Score, Settings
from amlcheck.core.address import detect
from amlcheck.core.audit import AuditRecord, append, verify
from amlcheck.core.clock import fixed
from amlcheck.core.engine import screen
from amlcheck.core.models import Address, Severity, SourceResult, SourceStatus, Verdict
from amlcheck.core.risk import Exposure
from amlcheck.core.rules import finding
from amlcheck.core.score import from_json
from amlcheck.intel import registry
from amlcheck.screening.base import SourceHealth
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


# AT-42 through a check (v2): the §7.11 trace's exposures make the score; it is stored (hashed)
# and put in the registry with its version.
async def test_check_carries_its_score(conn: sqlite3.Connection) -> None:
    result = await screen(
        detect(T),
        [traced(conn), Source("exposure", rules=("R-HEU-01",))],
        conn=conn,
        settings=Settings(),
        now=fixed(NOW),
    )
    assert result.score is not None
    # H_in = 0.6 × (1.0 × 0.2 + 0.5 × 0.1 × 0.8) = 0.144 → X 68.4; B 5 → ⌊68.4 + 31.6 × 0.05 + 0.5⌋
    assert (result.score.score, result.score.level) == (70, "moderate")
    assert result.score.breakdown == "exposure 68.4 · behaviour 5"
    assert result.verdict is Verdict.REVIEW  # R-SCR-01 at 31 (D-072); the other rules are INFO
    assert {f.rule_id: f.severity for f in result.findings}["R-SCR-01"] is Severity.REVIEW
    assert {f.severity for f in result.findings if f.rule_id != "R-SCR-01"} == {Severity.INFO}
    stored = conn.execute(
        "SELECT score_json FROM checks WHERE check_id = ?", (result.check_id,)
    ).fetchone()[0]
    assert from_json(stored) == result.score
    assert json.loads(stored)["hazard"] == {"in": "0.144", "out": "0"}
    cp = registry.get(conn, result.address.chain, result.address.norm)
    assert cp is not None
    assert (cp.last_score, cp.last_score_version) == (70, 2)
    registry.rebuild(conn)
    again = registry.get(conn, result.address.chain, result.address.norm)
    assert again is not None
    assert (again.last_score, again.last_score_version) == (70, 2)  # rebuilt from score_json


async def test_no_exposure_scores_behaviour_only(conn: sqlite3.Connection) -> None:
    result = await screen(
        detect(T),
        [Source("ofac_sdn"), Source("exposure", rules=("R-EXP-01", "R-HEU-02"))],
        conn=conn,
        settings=Settings(),
        now=fixed(NOW),
    )
    assert result.score is not None
    assert result.score.shown == "10 · low"  # R-EXP-01 is in the exposures, not in points
    assert result.verdict is Verdict.REVIEW  # R-EXP-01 is REVIEW by itself (D-072)


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
    assert gap.score.shown == "≥ 20 · low+"


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


class Exposed:
    """A source whose evidence carries one direct exposure, and the given rules."""

    source = "exposure"
    label = "USDT history (exposure)"
    required = True
    timeout: float | None = None

    def __init__(self, percent: str, *rules: str) -> None:
        self.percent = percent
        self.rules = rules

    async def check(self, address: Address) -> SourceResult:
        e = Exposure("in", 1, "TS", "sanctioned", "x", Decimal(100), Decimal(self.percent), ("T",))
        found = tuple(finding(r, self.source, r, NOW) for r in self.rules)
        evidence = {"exposures": [e.to_json()]}
        return SourceResult(
            self.source, self.label, True, SourceStatus.OK, NOW, found, "ok", evidence
        )

    async def health(self) -> SourceHealth:
        return SourceHealth(self.source, self.label, SourceStatus.OK, None, "test")


# AT-63: v2 verdict defaults. Behaviour and trace rules are INFO; a score from 31 is REVIEW through
# R-SCR-01; R-EXP-01 is REVIEW on its own; an override brings a v1 REVIEW back.
async def test_at63_verdict_defaults(conn: sqlite3.Connection) -> None:
    low = await screen(
        detect(T),
        [Exposed("0.01", "R-HEU-02", "R-TRC-04")],
        conn=conn,
        settings=Settings(),
        now=fixed(NOW),
    )
    assert low.score is not None
    assert (low.score.score, low.verdict) == (17, Verdict.NO_HITS)  # X 7.7 + B 10 → 17, low
    assert {f.severity for f in low.findings} == {Severity.INFO}
    moderate = await screen(
        detect(T), [Exposed("0.0454")], conn=conn, settings=Settings(), now=fixed(NOW)
    )
    assert moderate.score is not None
    assert (moderate.score.score, moderate.verdict) == (31, Verdict.REVIEW)
    (scr,) = moderate.findings
    assert scr.rule_id == "R-SCR-01"
    assert scr.evidence == {"score": 31, "review_at": 31, "level": "moderate"}
    direct = await screen(
        detect(T), [Exposed("0.001", "R-EXP-01")], conn=conn, settings=Settings(), now=fixed(NOW)
    )
    assert direct.verdict is Verdict.REVIEW
    v1_like = Settings(rules=Rules(severity={"R-HEU-02": "REVIEW"}))
    again = await screen(
        detect(T), [Exposed("0.01", "R-HEU-02")], conn=conn, settings=v1_like, now=fixed(NOW)
    )
    assert again.verdict is Verdict.REVIEW
    assert verify(conn).ok


@pytest.mark.parametrize(
    ("review_at", "verdict", "scr"),
    [(0, Verdict.NO_HITS, False), (55, Verdict.REVIEW, True), (56, Verdict.NO_HITS, False)],
)
async def test_r_scr_01_threshold(
    conn: sqlite3.Connection, review_at: int, verdict: Verdict, scr: bool
) -> None:
    settings = Settings(score=Score(review_at=review_at))
    result = await screen(detect(T), [Exposed("0.1")], conn=conn, settings=settings, now=fixed(NOW))
    assert result.score is not None
    assert result.score.shown == "55 · moderate"  # 10% sanctioned, direct
    assert result.verdict is verdict
    assert ("R-SCR-01" in {f.rule_id for f in result.findings}) is scr


async def test_r_scr_01_not_added_to_block(conn: sqlite3.Connection) -> None:
    settings = Settings(score=Score(review_at=1))
    result = await screen(
        detect(T),
        [Source("ofac_sdn", rules=("R-SAN-01",))],
        conn=conn,
        settings=settings,
        now=fixed(NOW),
    )
    assert result.verdict is Verdict.BLOCK
    assert [f.rule_id for f in result.findings] == ["R-SAN-01"]
