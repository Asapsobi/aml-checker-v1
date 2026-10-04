"""The trace as a check source (PRD F9.4, methodology §7.7).

Runs when a check's amount is at least `[trace] auto_amount_usdt` or a trace is asked for. It is a
required source while it runs: a failed read or the time budget makes it `stale` → INCOMPLETE, with
the partial trace kept for display. The trace's id goes into the check's audit record.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

from amlcheck.config import Settings
from amlcheck.core import risk
from amlcheck.core.clock import Clock, utcnow
from amlcheck.core.models import Address, SourceResult, SourceStatus
from amlcheck.core.score import hazard
from amlcheck.intel.names import entity_name
from amlcheck.screening.base import SourceHealth
from amlcheck.trace.engine import TraceEngine, TraceFailed
from amlcheck.trace.jobs import TraceJobs
from amlcheck.trace.model import Trace, dec, pct
from amlcheck.trace.rules import trace_findings

SOURCE = "trace"
LABEL = "Source-of-funds trace"


def summary(trace: Trace, name: Callable[[str, str], str] | None = None) -> dict[str, Any]:
    """The trace's part of the check record. With `name`, also its indirect exposures (§11.1)."""
    exposures = risk.from_trace(trace, name) if name is not None else []
    return {
        "partition": {k: dec(v) for k, v in sorted(trace.partition.items(), key=lambda kv: -kv[1])},
        "coverage": dec(trace.coverage) if trace.coverage is not None else None,
        "layering": dec(trace.annotations.get("layering", Decimal(0))),
        "hazard": dec(hazard(trace)),  # the score's H (methodology §9), kept with the check
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
    }


class TraceSource:
    source = SOURCE
    label = LABEL
    required = True

    def __init__(
        self, jobs: TraceJobs, engine: TraceEngine, settings: Settings, *, clock: Clock = utcnow
    ) -> None:
        self._jobs = jobs
        self._engine = engine
        self._s = settings
        self._clock = clock
        self.timeout: float | None = float(settings.trace.time_budget_seconds + 60)

    def _namer(self, address: Address) -> Callable[[str, str], str]:
        return lambda a, category: entity_name(self._engine.conn, address.chain, a, category)

    async def check(self, address: Address) -> SourceResult:
        trace_id = self._jobs.create(address.chain, address.norm, "in", requested_by="check")
        try:
            trace = await self._jobs.run(trace_id, self._engine)
        except TraceFailed as e:
            return SourceResult(
                SOURCE,
                LABEL,
                True,
                SourceStatus.STALE,
                self._clock(),
                (),
                f"trace incomplete: {e.reason}",
                {
                    "trace_id": trace_id,
                    **summary(e.partial, self._namer(address)),
                    "complete": False,
                },
            )
        now = self._clock()
        findings = tuple(trace_findings(trace, self._s.trace, now))
        coverage = pct(trace.coverage) if trace.coverage is not None else "no inflow"
        b = trace.budget
        detail = (
            f"coverage {coverage}; {b.nodes_read} address(es) read, {b.queries} provider call(s), "
            f"{b.seconds:.0f} s"
        )
        return SourceResult(
            SOURCE,
            LABEL,
            True,
            SourceStatus.OK,
            now,
            findings,
            detail,
            {"trace_id": trace_id, **summary(trace, self._namer(address)), "complete": True},
        )

    async def health(self) -> SourceHealth:
        return SourceHealth(
            SOURCE, LABEL, SourceStatus.OK, None, "runs on large or requested checks"
        )
