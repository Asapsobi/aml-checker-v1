"""screen(): sources → rules → verdict → audit (architecture §4.1).

1. Run every source concurrently, each with its own timeout. A source that raises or times out
   becomes `failed()` (CLAUDE.md: never an exception out of the engine).
2. Collect findings, apply config severity overrides, add R-SYS-01 for required gaps, decide.
3. Append the audit record **before** anything is returned for display (non-negotiable #2). If the
   append fails, the check fails: a result that isn't recorded is never shown.
Score (P7) and registry (P4) join later.
"""

from __future__ import annotations

import asyncio
import sqlite3
import uuid
from collections.abc import Sequence
from decimal import Decimal
from importlib.metadata import version

from amlcheck.chain.base import canonical_amount
from amlcheck.config import Settings
from amlcheck.core.audit import AuditFinding, AuditRecord, AuditSource, append
from amlcheck.core.clock import Clock, to_db, to_iso, utcnow
from amlcheck.core.models import Address, CheckResult, Finding, SourceResult, SourceStatus
from amlcheck.core.rules import RULES_VERSION, apply_overrides, system_findings
from amlcheck.core.verdict import decide
from amlcheck.net.http import SourceError
from amlcheck.screening.base import SourceAdapter, failed

DEFAULT_SOURCE_TIMEOUT_S = 120.0


async def _run(adapter: SourceAdapter, address: Address, clock: Clock) -> SourceResult:
    timeout = adapter.timeout if adapter.timeout is not None else DEFAULT_SOURCE_TIMEOUT_S
    try:
        result = await asyncio.wait_for(adapter.check(address), timeout)
    except TimeoutError:
        return failed(adapter, clock(), f"no answer within {timeout:g} s")
    except SourceError as e:
        return failed(adapter, clock(), e.reason)
    except Exception as e:  # a bug in one source must not take the check down
        return failed(adapter, clock(), f"internal error ({type(e).__name__}: {e})")
    if result.source != adapter.source or result.required != adapter.required:
        return failed(adapter, clock(), "source answered for another source")
    return result


async def screen(
    address: Address,
    sources: Sequence[SourceAdapter],
    *,
    conn: sqlite3.Connection,
    settings: Settings,
    amount: Decimal | None = None,
    client: str | None = None,
    note: str | None = None,
    now: Clock = utcnow,
) -> CheckResult:
    started = now()
    results = await asyncio.gather(*(_run(s, address, now) for s in sources))
    ordered = tuple(sorted(results, key=lambda r: r.source))
    found: list[Finding] = [f for r in ordered for f in r.findings]
    findings = apply_overrides(found, settings.rules.severity) + system_findings(ordered, started)
    findings.sort(key=lambda f: (f.rule_id, f.source, f.summary))
    verdict = decide(findings)
    check_id = str(uuid.uuid4())
    tool_version = version("amlcheck")
    config_hash = settings.hash()
    record = AuditRecord(
        check_id=check_id,
        created_at=to_db(started),  # fixed width: `audit list --since` compares it as text
        chain=address.chain.value,
        address_norm=address.norm,
        verdict=verdict.value,
        tool_version=tool_version,
        rules_version=RULES_VERSION,
        config_hash=config_hash,
        sources=tuple(
            AuditSource(
                source=r.source,
                required=r.required,
                status=r.status.value,
                summary=r.detail or r.status.value,
                evidence=r.evidence,
                as_of=to_iso(r.observed_at),
            )
            for r in ordered
        ),
        findings=tuple(
            AuditFinding(
                rule_id=f.rule_id,
                severity=f.severity.value,
                source=f.source,
                summary=f.summary,
                observed_at=to_iso(f.observed_at),
                evidence=f.evidence,
            )
            for f in findings
        ),
        amount=canonical_amount(amount) if amount is not None else None,
        client=client,
        operator_note=note,
    )
    appended = append(conn, record)  # before anything is shown
    return CheckResult(
        address=address,
        verdict=verdict,
        checked_at=started,
        sources=ordered,
        findings=tuple(findings),
        tool_version=tool_version,
        config_hash=config_hash,
        check_id=check_id,
        record_hash=appended.record_hash,
        amount=amount,
        client=client,
        note=note,
    )


def gaps(result: CheckResult) -> list[SourceResult]:
    """Required sources that kept the check from being complete."""
    return [
        s
        for s in result.sources
        if s.required and s.status in (SourceStatus.ERROR, SourceStatus.STALE)
    ]
