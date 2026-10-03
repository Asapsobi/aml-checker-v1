"""Verdict precedence (methodology §2.5, PRD §7.1): BLOCK > INCOMPLETE > REVIEW > NO_HITS.

A sanctions hit blocks even if another source failed (AT-16). Low priority is evidence, not a
severity, so it never changes the verdict.
"""

from __future__ import annotations

from collections.abc import Iterable

from amlcheck.core.models import Finding, Severity, Verdict


def decide(findings: Iterable[Finding]) -> Verdict:
    severities = {f.severity for f in findings}
    if Severity.BLOCK in severities:
        return Verdict.BLOCK
    if Severity.INCOMPLETE in severities:
        return Verdict.INCOMPLETE
    if Severity.REVIEW in severities:
        return Verdict.REVIEW
    return Verdict.NO_HITS
