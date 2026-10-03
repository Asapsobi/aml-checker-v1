"""Verdict precedence (methodology §2.5, PRD §7.1): BLOCK > INCOMPLETE > REVIEW > NO_HITS.

A sanctions hit blocks even if another source failed (AT-16). Low priority is evidence, not a
severity, so it never changes the verdict.
"""

from __future__ import annotations

from collections.abc import Iterable

from amlcheck.core.models import Finding, Severity, Verdict

#: What the operator should do, shown with every verdict (CLI, case report).
ACTION = {
    Verdict.BLOCK: "Do not transact. Escalate.",
    Verdict.INCOMPLETE: "A required source failed or is stale. Retry, or treat as REVIEW.",
    Verdict.REVIEW: "Review by hand before transacting.",
    Verdict.NO_HITS: "Proceed per policy. Not a clearance.",
}
DISCLAIMER = (
    "Internal use only. NO_HITS means nothing was found in the sources checked, as of the times "
    "shown; it is not a clearance."
)


def decide(findings: Iterable[Finding]) -> Verdict:
    severities = {f.severity for f in findings}
    if Severity.BLOCK in severities:
        return Verdict.BLOCK
    if Severity.INCOMPLETE in severities:
        return Verdict.INCOMPLETE
    if Severity.REVIEW in severities:
        return Verdict.REVIEW
    return Verdict.NO_HITS
