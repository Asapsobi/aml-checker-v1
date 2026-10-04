"""The trace as a check source (PRD F9.4, methodology §7.7).

Runs when a check's amount is at least `[trace] auto_amount_usdt` or a trace is asked for. It is a
required source while it runs: a failed read or the time budget makes it `stale` → INCOMPLETE, with
the partial trace kept for display. The trace's id goes into the check's audit record.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from amlcheck.config import Settings
from amlcheck.core import risk
from amlcheck.core.clock import Clock, utcnow
from amlcheck.core.models import Address, SourceResult, SourceStatus
from amlcheck.intel.names import entity_name
from amlcheck.screening.base import SourceHealth
from amlcheck.trace.engine import TraceEngine, TraceFailed
from amlcheck.trace.jobs import TraceJobs
from amlcheck.trace.model import Trace, dec, pct
from amlcheck.trace.rules import trace_findings

SOURCE = "trace"
LABEL = "Trace (in and out)"


def summary(
    trace: Trace, name: Callable[[str, str], str] | None = None, indirect: str = "path"
) -> dict[str, Any]:
    """The trace's part of the check record. With `name`, also its indirect exposures (§11.1),
    their volume by the `indirect` method (D-078)."""
    exposures = risk.from_trace(trace, name, indirect) if name is not None else []
    return {
        "partition": {k: dec(v) for k, v in sorted(trace.partition.items(), key=lambda kv: -kv[1])},
        "coverage": dec(trace.coverage) if trace.coverage is not None else None,
        "layering": dec(trace.annotations.get("layering", Decimal(0))),
        "target_inflow_usdt": dec(trace.target_inflow),
        "top_paths": [
            {
                "to_category": p.to_category,
                "addresses": list(p.addresses),
                "bottleneck_usdt": dec(p.bottleneck),
                "estimated_usdt": dec(p.estimated),
            }
            for p in trace.paths[:5]
        ],
        "budget": {  # no floats in an audit record (architecture §4.1)
            "nodes_read": trace.budget.nodes_read,
            "queries": trace.budget.queries,
            "cache_hits": trace.budget.cache_hits,
            "seconds": f"{trace.budget.seconds:.1f}",
        },
        "estimated": True,  # shares are proportional estimates (D-016)
        "exposures": [e.to_json() for e in exposures],
        "indirect_volume": indirect if name is not None else None,
    }


class TraceSource:
    """Both directions at once, one deadline (methodology §12.2, D-079): where the money came from
    and where it went. The inbound trace's id is the check's `trace_id`; the outbound one is in the
    evidence under `out`. Its findings (R-TRC) read the inbound trace; both give exposures."""

    source = SOURCE
    label = LABEL
    required = True

    def __init__(
        self,
        jobs: TraceJobs,
        engine: TraceEngine,
        settings: Settings,
        *,
        clock: Clock = utcnow,
        outbound: TraceEngine | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._jobs = jobs
        self._engines = {"in": engine, "out": outbound or engine}
        self._s = settings
        self._clock = clock
        self._monotonic = monotonic
        self.timeout: float | None = float(settings.trace.time_budget_seconds + 60)

    def _namer(self, address: Address) -> Callable[[str, str], str]:
        conn = self._engines["in"].conn
        return lambda a, category: entity_name(conn, address.chain, a, category)

    async def _one(
        self, trace_id: str, direction: str, deadline: float
    ) -> tuple[Trace, str | None]:
        try:
            trace = await self._jobs.run(trace_id, self._engines[direction], deadline=deadline)
        except TraceFailed as e:
            return e.partial, e.reason
        return trace, None

    async def check(self, address: Address) -> SourceResult:
        deadline = self._monotonic() + self._s.trace.time_budget_seconds
        ids = {
            d: self._jobs.create(address.chain, address.norm, d, requested_by="check")
            for d in ("in", "out")
        }
        (t_in, fail_in), (t_out, fail_out) = await asyncio.gather(
            self._one(ids["in"], "in", deadline), self._one(ids["out"], "out", deadline)
        )
        name, method = self._namer(address), self._s.score.indirect
        s_in, s_out = summary(t_in, name, method), summary(t_out, name, method)
        evidence: dict[str, Any] = {
            "trace_id": ids["in"],
            **s_in,
            "complete": fail_in is None,
            "exposures": s_in["exposures"] + s_out["exposures"],
            "out": {
                "trace_id": ids["out"],
                **{k: v for k, v in s_out.items() if k != "exposures"},
                "complete": fail_out is None,
            },
        }
        now = self._clock()
        if fail_in is not None or fail_out is not None:
            which, reason = ("in", fail_in) if fail_in is not None else ("out", fail_out)
            what = "source of funds" if which == "in" else "destination of funds"
            detail = f"trace incomplete ({what}): {reason}"
            return SourceResult(SOURCE, LABEL, True, SourceStatus.STALE, now, (), detail, evidence)
        findings = tuple(trace_findings(t_in, self._s.trace, now))
        return SourceResult(
            SOURCE, LABEL, True, SourceStatus.OK, now, findings, _detail(t_in, t_out), evidence
        )

    async def health(self) -> SourceHealth:
        return SourceHealth(
            SOURCE, LABEL, SourceStatus.OK, None, "runs on large or requested checks"
        )


def _detail(t_in: Trace, t_out: Trace) -> str:
    def coverage(t: Trace) -> str:
        return pct(t.coverage) if t.coverage is not None else "nothing to trace"

    nodes = t_in.budget.nodes_read + t_out.budget.nodes_read
    queries = t_in.budget.queries + t_out.budget.queries
    seconds = max(t_in.budget.seconds, t_out.budget.seconds)
    stopped = " · stopped at the time budget" if "time" in (t_in.stopped, t_out.stopped) else ""
    return (
        f"in: coverage {coverage(t_in)}; out: coverage {coverage(t_out)}; {nodes} address(es) "
        f"read, {queries} provider call(s), {seconds:.0f} s{stopped}"
    )
