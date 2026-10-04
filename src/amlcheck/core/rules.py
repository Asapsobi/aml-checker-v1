"""Rule IDs, default severities, overrides, R-SYS-01, fixed rules.

Config refuses unknown IDs and forbidden overrides at load (AT-02); `severity_for` applies the rest.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import datetime
from typing import Any

from amlcheck.core.models import Finding, Severity, SourceResult, SourceStatus

#: Methodology §11.5, version 2 (D-072): BLOCK only for facts about the address itself; REVIEW for
#: the fact-based rules and for R-SCR-01 (score ≥ `[score] review_at`, 31 by default); the other
#: rules are INFO and feed the score. `[rules] severity` can set any of them back.
DEFAULT_SEVERITY: dict[str, Severity] = {
    "R-SAN-01": Severity.BLOCK,
    "R-FRZ-01": Severity.BLOCK,
    "R-FRZ-02": Severity.REVIEW,
    "R-SYS-01": Severity.INCOMPLETE,
    "R-EXP-01": Severity.REVIEW,
    "R-EXP-02": Severity.INFO,
    "R-HEU-01": Severity.INFO,
    "R-HEU-02": Severity.INFO,
    "R-HEU-03": Severity.INFO,
    "R-HEU-04": Severity.INFO,
    "R-HEU-05": Severity.INFO,
    "R-HEU-06": Severity.REVIEW,
    "R-HEU-07": Severity.INFO,
    "R-TRC-01": Severity.INFO,
    "R-TRC-02": Severity.INFO,
    "R-TRC-03": Severity.INFO,
    "R-TRC-04": Severity.INFO,
    "R-TRC-05": Severity.INFO,
    "R-SCR-01": Severity.REVIEW,
}

#: Severity can't be changed at all (PRD §7.2 "fixed").
FIXED: frozenset[str] = frozenset({"R-SYS-01"})

#: Inferences never block (methodology §2.5, D-017), nor does the score (D-051).
NEVER_BLOCK: frozenset[str] = frozenset({"R-HEU-07", "R-TRC-05", "R-SCR-01"})

#: A sanctions or freeze hit on the address itself always changes the verdict: never INFO.
NEVER_INFO: frozenset[str] = frozenset({"R-SAN-01", "R-FRZ-01"})

#: Stored with every check (methodology: "Screening + behaviour rules").
RULES_VERSION = 2  # D-072: the v2 default severities

SYSTEM_SOURCE = "engine"


def severity_for(rule_id: str, overrides: Mapping[str, str]) -> Severity:
    """The rule's severity after config overrides; fixed rules ignore overrides (PRD §7.2)."""
    if rule_id not in DEFAULT_SEVERITY:
        raise KeyError(f"unknown rule {rule_id}")
    if rule_id in FIXED or rule_id not in overrides:
        return DEFAULT_SEVERITY[rule_id]
    severity = Severity(overrides[rule_id])
    if rule_id in NEVER_BLOCK and severity is Severity.BLOCK:  # config refuses it; belt and braces
        return DEFAULT_SEVERITY[rule_id]
    if rule_id in NEVER_INFO and severity is Severity.INFO:
        return DEFAULT_SEVERITY[rule_id]
    return severity


def finding(
    rule_id: str,
    source: str,
    summary: str,
    observed_at: datetime,
    evidence: Mapping[str, Any] | None = None,
    *,
    overrides: Mapping[str, str] | None = None,
) -> Finding:
    return Finding(
        rule_id=rule_id,
        severity=severity_for(rule_id, overrides or {}),
        source=source,
        summary=summary,
        observed_at=observed_at,
        evidence=dict(evidence or {}),
    )


def apply_overrides(findings: Iterable[Finding], overrides: Mapping[str, str]) -> list[Finding]:
    """Re-derive each finding's severity from config (methodology §2.5: before `decide`)."""
    return [replace(f, severity=severity_for(f.rule_id, overrides)) for f in findings]


def system_findings(sources: Iterable[SourceResult], now: datetime) -> list[Finding]:
    """R-SYS-01 for every required source that errored or is stale (non-negotiable #1).

    `skipped` is not a gap (methodology §2.1).
    """
    out = []
    for s in sorted(sources, key=lambda s: s.source):
        if s.required and s.status in (SourceStatus.ERROR, SourceStatus.STALE):
            out.append(
                finding(
                    "R-SYS-01",
                    SYSTEM_SOURCE,
                    f"{s.label} {s.status.value}: {s.detail or 'no detail'}",
                    now,
                    {"source": s.source, "status": s.status.value, "detail": s.detail},
                )
            )
    return out
