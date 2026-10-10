import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from amlcheck.config import Settings
from amlcheck.core.address import detect
from amlcheck.core.audit import verify
from amlcheck.core.clock import fixed, to_db
from amlcheck.core.engine import screen
from amlcheck.core.models import Chain, Verdict
from amlcheck.storage.db import open_db, transaction
from amlcheck.trace.adapter import TraceSource
from amlcheck.trace.engine import TraceEngine, TraceFailed
from amlcheck.trace.jobs import TraceJobs
from tests.unit.trace_world import NOW, E, Fake, T, engine, example, setup_example


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / "a.db")


def jobs(conn: sqlite3.Connection, minutes: float = 0) -> TraceJobs:
    return TraceJobs(conn, Settings(), clock=fixed(NOW + timedelta(minutes=minutes)))


def world(
    conn: sqlite3.Connection, fail_on: frozenset[str] = frozenset()
) -> tuple[TraceEngine, Fake]:
    fake = Fake(example(), fail_on=set(fail_on))
    eng, store = engine(conn, fake)
    setup_example(conn, store)
    return eng, fake


# --- the job queue (T-6.08) -----------------------------------------------------------------------


async def test_job_runs_and_stores_its_result(conn: sqlite3.Connection) -> None:
    eng, _ = world(conn)
    q = jobs(conn)
    tid = q.create(Chain.BSC, T)
    job = q.get(tid)
    assert job is not None
    assert (job.status, job.requested_by, job.result) == ("queued", "cli", None)
    trace = await q.run(tid, eng)
    done = q.get(tid)
    assert done is not None
    assert done.status == "done"
    assert done.finished_at is not None
    assert done.result == trace
    assert q.latest(Chain.BSC, T) == done
    settings = conn.execute("SELECT settings_json FROM traces").fetchone()[0]
    assert '"trace_version": 4' in settings
    assert '"max_nodes": 100' in settings
    assert '"bsc_max_nodes": 50' in settings


async def test_failed_job_keeps_partial_and_reason(conn: sqlite3.Connection) -> None:
    eng, _ = world(conn, frozenset({E}))
    q = jobs(conn)
    tid = q.create(Chain.BSC, T)
    with pytest.raises(TraceFailed):
        await q.run(tid, eng)
    job = q.get(tid)
    assert job is not None
    assert (job.status, job.result) == ("failed", None)
    assert job.partial is not None
    assert not job.partial.complete
    assert job.failure_reason is not None
    assert E in job.failure_reason


def test_idempotency_key_returns_the_same_job(conn: sqlite3.Connection) -> None:
    q = jobs(conn)
    first = q.create(Chain.BSC, T, idempotency_key="req-1")
    assert q.create(Chain.BSC, T, idempotency_key="req-1") == first
    assert q.create(Chain.BSC, T, idempotency_key="req-2") != first
    assert q.create(Chain.BSC, T) != q.create(Chain.BSC, T)  # no key: always a new job
    assert conn.execute("SELECT count(*) FROM traces").fetchone()[0] == 4


def test_claim_is_atomic(conn: sqlite3.Connection) -> None:
    q = jobs(conn)
    tid = q.create(Chain.BSC, T)
    assert q.claim(tid)
    assert not q.claim(tid)  # a second worker gets nothing
    assert not jobs(conn, minutes=5).claim(tid)  # still inside the time budget + margin
    assert q.pending() == []


# A process died mid-trace: the job is `running` with an old start. Once older than the time budget
# plus 120 s, the worker claims and finishes it (architecture §4.2).
async def test_abandoned_job_is_resumed(conn: sqlite3.Connection) -> None:
    eng, _ = world(conn)
    q = jobs(conn)
    tid = q.create(Chain.BSC, T)
    assert q.claim(tid)
    s = Settings().trace
    early = jobs(conn, minutes=(s.time_budget_seconds + 60) / 60)
    assert early.pending() == []
    assert await early.run_pending(lambda chain: eng) == []
    late = jobs(conn, minutes=(s.time_budget_seconds + 121) / 60)
    assert late.pending() == [tid]
    assert await late.run_pending(lambda chain: eng) == [tid]
    job = q.get(tid)
    assert job is not None
    assert job.status == "done"
    assert job.result is not None
    assert job.result.complete


async def test_run_pending_oldest_first_and_failures_stored(conn: sqlite3.Connection) -> None:
    eng, _ = world(conn, frozenset({E}))
    a = jobs(conn).create(Chain.BSC, T)
    b = jobs(conn, minutes=1).create(Chain.BSC, T)
    assert await jobs(conn, minutes=2).run_pending(lambda chain: eng) == [a, b]
    statuses = {r[0] for r in conn.execute("SELECT status FROM traces")}
    assert statuses == {"failed"}


async def test_finished_job_is_not_rerun(conn: sqlite3.Connection) -> None:
    eng, fake = world(conn)
    q = jobs(conn)
    tid = q.create(Chain.BSC, T)
    first = await q.run(tid, eng)
    asked = len(fake.asked)
    assert await q.run(tid, eng) == first
    assert len(fake.asked) == asked


async def test_progress_is_written(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("amlcheck.trace.jobs.PROGRESS_EVERY_S", 0.0)
    eng, _ = world(conn)
    q = jobs(conn)
    tid = q.create(Chain.BSC, T)
    await q.run(tid, eng)
    job = q.get(tid)
    assert job is not None
    assert job.progress is not None
    assert job.progress["nodes_read"] >= 1


# --- the trace as a check source (T-6.07) ---------------------------------------------------------


async def test_trace_source_in_a_check(conn: sqlite3.Connection) -> None:
    eng, _ = world(conn)
    src = TraceSource(jobs(conn), eng, Settings(), clock=fixed(NOW))
    result = await screen(detect(T), [src], conn=conn, settings=Settings(), now=fixed(NOW))
    # Methodology §7.11: R-TRC-01, R-TRC-03 and R-TRC-05 (INFO); the exposures score 69, so
    # R-SCR-01 makes it REVIEW (D-072).
    assert {f.rule_id for f in result.findings} == {"R-TRC-01", "R-TRC-03", "R-TRC-05", "R-SCR-01"}
    assert result.verdict is Verdict.REVIEW
    assert result.score is not None
    assert result.score.shown == "68 · moderate"
    (r,) = result.sources
    assert r.evidence["complete"] is True
    assert r.evidence["coverage"] == "0.9"
    assert r.evidence["partition"]["sanctioned"] == "0.2"
    assert r.evidence["top_paths"][0]["addresses"][0] == T
    assert [(e["category"], e["hop"]) for e in r.evidence["exposures"]] == [
        ("sanctioned", 2),
        ("suspicious_collector", 2),
    ]
    trace_id = r.evidence["trace_id"]
    job = jobs(conn).get(trace_id)
    assert job is not None
    assert (job.status, job.requested_by) == ("done", "check")
    row = conn.execute("SELECT trace_id FROM checks WHERE check_id = ?", (result.check_id,))
    assert row.fetchone()[0] == trace_id
    assert verify(conn).ok


# AT-39 inside a check: a failed read → the trace is stale → INCOMPLETE, partial kept.
async def test_trace_source_failure_is_incomplete(conn: sqlite3.Connection) -> None:
    eng, _ = world(conn, frozenset({E}))
    src = TraceSource(jobs(conn), eng, Settings(), clock=fixed(NOW))
    result = await screen(detect(T), [src], conn=conn, settings=Settings(), now=fixed(NOW))
    assert result.verdict is Verdict.INCOMPLETE
    sys01 = next(f for f in result.findings if f.rule_id == "R-SYS-01")
    assert E in sys01.summary  # names the node (AT-39)
    assert "hop 2" in sys01.summary
    (r,) = result.sources
    assert r.evidence["complete"] is False
    assert r.evidence["partition"]["sanctioned"] == "0.2"
    assert E in (r.detail or "")
    job = jobs(conn).get(r.evidence["trace_id"])
    assert job is not None
    assert job.status == "failed"
    row = conn.execute("SELECT trace_id FROM checks WHERE check_id = ?", (result.check_id,))
    assert row.fetchone()[0] == r.evidence["trace_id"]


def test_trace_source_timeout_covers_the_budget() -> None:
    s = Settings()
    src = TraceSource.__new__(TraceSource)
    TraceSource.__init__(src, None, None, s)  # type: ignore[arg-type]
    assert src.timeout == s.trace.time_budget_seconds + 60
    assert src.required


def test_stale_running_uses_db_time(conn: sqlite3.Connection) -> None:
    q = jobs(conn)
    tid = q.create(Chain.BSC, T)
    with transaction(conn):
        conn.execute(
            "UPDATE traces SET status = 'running', started_at = ? WHERE trace_id = ?",
            (to_db(NOW - timedelta(hours=1)), tid),
        )
    assert q.pending() == [tid]
    assert q.claim(tid)
