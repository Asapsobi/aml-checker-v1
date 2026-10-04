"""Trace jobs: persistent, one worker, progress, resume (PRD F9.5, architecture §4.2).

A trace is a row in `traces`: queued → running → done | failed. Claiming a job is one atomic UPDATE,
so two processes never run the same trace, and no file lock is needed (works the same on Windows).
A job left `running` by a process that died is claimed again once it is older than the time budget
plus a margin; the transfer cache makes the re-run cheap. Progress is written at most once a second.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from amlcheck.config import Settings
from amlcheck.core.address import detect
from amlcheck.core.clock import Clock, from_iso, to_db, utcnow
from amlcheck.core.models import Chain
from amlcheck.storage.db import transaction
from amlcheck.trace.engine import Direction, TraceEngine, TraceFailed, TraceProgress
from amlcheck.trace.model import TRACE_VERSION, Trace

PROGRESS_EVERY_S = 1.0


@dataclass(frozen=True)
class Job:
    trace_id: str
    chain: Chain
    address: str
    direction: Direction
    status: str
    requested_by: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    progress: dict[str, Any] | None
    result: Trace | None
    partial: Trace | None
    failure_reason: str | None


class TraceJobs:
    def __init__(self, conn: sqlite3.Connection, settings: Settings, *, clock: Clock = utcnow):
        self._conn = conn
        self._s = settings
        self._clock = clock

    def create(
        self,
        chain: Chain,
        address: str,
        direction: Direction = "in",
        *,
        requested_by: str = "cli",
        idempotency_key: str | None = None,
    ) -> str:
        if idempotency_key:
            row = self._conn.execute(
                "SELECT trace_id FROM traces WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if row:
                return str(row[0])
        trace_id = str(uuid.uuid4())
        now = to_db(self._clock())
        settings = {**self._s.trace.model_dump(mode="json"), "trace_version": TRACE_VERSION}
        with transaction(self._conn):
            self._conn.execute(
                "INSERT INTO traces (trace_id, chain, address_norm, direction, status, "
                "requested_by, idempotency_key, settings_json, as_of, created_at) "
                "VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?)",
                (
                    trace_id,
                    chain.value,
                    address,
                    direction,
                    requested_by,
                    idempotency_key,
                    json.dumps(settings, sort_keys=True),
                    now,
                    now,
                ),
            )
        return trace_id

    def claim(self, trace_id: str) -> bool:
        """Queued, or running but abandoned → running (atomic)."""
        now = self._clock()
        stale = now - timedelta(seconds=self._s.trace.time_budget_seconds + 120)
        with transaction(self._conn):
            cur = self._conn.execute(
                "UPDATE traces SET status = 'running', started_at = ? WHERE trace_id = ? "
                "AND (status = 'queued' OR (status = 'running' AND started_at < ?))",
                (to_db(now), trace_id, to_db(stale)),
            )
        return cur.rowcount == 1

    def pending(self) -> list[str]:
        stale = self._clock() - timedelta(seconds=self._s.trace.time_budget_seconds + 120)
        rows = self._conn.execute(
            "SELECT trace_id FROM traces WHERE status = 'queued' "
            "OR (status = 'running' AND started_at < ?) ORDER BY created_at, trace_id",
            (to_db(stale),),
        ).fetchall()
        return [r[0] for r in rows]

    def _progress_writer(
        self, trace_id: str, also: Callable[[TraceProgress], None] | None
    ) -> Callable[[TraceProgress], None]:
        last = [0.0]

        def write(p: TraceProgress) -> None:
            if also is not None:
                also(p)
            now = time.monotonic()
            if now - last[0] < PROGRESS_EVERY_S:
                return
            last[0] = now
            with transaction(self._conn):
                self._conn.execute(
                    "UPDATE traces SET progress_json = ? WHERE trace_id = ?",
                    (json.dumps(p.__dict__), trace_id),
                )

        return write

    async def run(
        self,
        trace_id: str,
        engine: TraceEngine,
        on_progress: Callable[[TraceProgress], None] | None = None,
        *,
        deadline: float | None = None,
    ) -> Trace:
        """Run a claimed or claimable job to the end; `TraceFailed` carries the partial result.
        `deadline` is shared by the two directions of a check's trace (methodology §12.2)."""
        job = self.get(trace_id)
        if job is None:
            raise KeyError(trace_id)
        if job.status != "running" and not self.claim(trace_id):
            if job.result is not None:
                return job.result
            raise RuntimeError(f"trace {trace_id} is {job.status} elsewhere")
        try:
            trace = await engine.run(
                detect(job.address),
                job.direction,
                self._progress_writer(trace_id, on_progress),
                deadline=deadline,
            )
        except TraceFailed as e:
            with transaction(self._conn):
                self._conn.execute(
                    "UPDATE traces SET status = 'failed', finished_at = ?, partial_json = ?, "
                    "failure_reason = ? WHERE trace_id = ?",
                    (to_db(self._clock()), json.dumps(e.partial.to_json()), e.reason, trace_id),
                )
            raise
        with transaction(self._conn):
            self._conn.execute(
                "UPDATE traces SET status = 'done', finished_at = ?, result_json = ? "
                "WHERE trace_id = ?",
                (to_db(self._clock()), json.dumps(trace.to_json()), trace_id),
            )
        return trace

    async def run_pending(self, engine_for: Callable[[Chain], TraceEngine]) -> list[str]:
        """The one worker: run every queued (or abandoned) job, oldest first."""
        done = []
        for trace_id in self.pending():
            job = self.get(trace_id)
            if job is None or not self.claim(trace_id):
                continue
            with contextlib.suppress(TraceFailed):  # the failure is stored with the job
                await self.run(trace_id, engine_for(job.chain))
            done.append(trace_id)
        return done

    def get(self, trace_id: str) -> Job | None:
        return read_job(self._conn, trace_id)

    def latest(self, chain: Chain, address: str) -> Job | None:
        r = self._conn.execute(
            "SELECT trace_id FROM traces WHERE chain = ? AND address_norm = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (chain.value, address),
        ).fetchone()
        return self.get(r[0]) if r else None


def read_job(conn: sqlite3.Connection, trace_id: str) -> Job | None:
    """A stored job, read only (the case report reads traces this way)."""
    r = conn.execute(
        "SELECT trace_id, chain, address_norm, direction, status, requested_by, created_at, "
        "started_at, finished_at, progress_json, result_json, partial_json, failure_reason "
        "FROM traces WHERE trace_id = ?",
        (trace_id,),
    ).fetchone()
    if r is None:
        return None
    return Job(
        trace_id=r[0],
        chain=Chain(r[1]),
        address=r[2],
        direction=r[3],
        status=r[4],
        requested_by=r[5],
        created_at=from_iso(r[6]),
        started_at=from_iso(r[7]) if r[7] else None,
        finished_at=from_iso(r[8]) if r[8] else None,
        progress=json.loads(r[9]) if r[9] else None,
        result=Trace.from_json(json.loads(r[10])) if r[10] else None,
        partial=Trace.from_json(json.loads(r[11])) if r[11] else None,
        failure_reason=r[12],
    )
