from datetime import UTC, datetime

import pytest

from amlcheck.core.models import Finding, Severity, SourceResult, SourceStatus, Verdict
from amlcheck.core.rules import apply_overrides, finding, severity_for, system_findings
from amlcheck.core.verdict import decide

NOW = datetime(2026, 10, 1, tzinfo=UTC)


def f(rule: str, overrides: dict[str, str] | None = None) -> Finding:
    return finding(rule, "src", "x", NOW, overrides=overrides)


def src(name: str, status: SourceStatus, required: bool = True) -> SourceResult:
    return SourceResult(name, name.upper(), required, status, NOW, detail="boom")


@pytest.mark.parametrize(
    ("rules", "verdict"),
    [
        ([], Verdict.NO_HITS),
        (["R-HEU-01"], Verdict.REVIEW),
        (["R-HEU-01", "R-SYS-01"], Verdict.INCOMPLETE),
        (["R-SYS-01", "R-FRZ-02"], Verdict.INCOMPLETE),
        (["R-SAN-01", "R-SYS-01"], Verdict.BLOCK),  # AT-16
        (["R-FRZ-01", "R-FRZ-02", "R-SYS-01"], Verdict.BLOCK),
    ],
)
def test_precedence(rules: list[str], verdict: Verdict) -> None:
    assert decide(f(r) for r in rules) == verdict


def test_defaults_and_overrides() -> None:
    assert severity_for("R-SAN-01", {}) is Severity.BLOCK
    assert severity_for("R-EXP-01", {}) is Severity.REVIEW
    assert severity_for("R-EXP-01", {"R-EXP-01": "BLOCK"}) is Severity.BLOCK
    assert severity_for("R-SYS-01", {"R-SYS-01": "REVIEW"}) is Severity.INCOMPLETE  # fixed
    assert severity_for("R-HEU-07", {"R-HEU-07": "BLOCK"}) is Severity.REVIEW  # never BLOCK
    with pytest.raises(KeyError):
        severity_for("R-NOPE-01", {})


def test_apply_overrides_changes_verdict() -> None:
    found = [f("R-EXP-01")]
    assert decide(found) is Verdict.REVIEW
    assert decide(apply_overrides(found, {"R-EXP-01": "BLOCK"})) is Verdict.BLOCK


def test_low_priority_is_evidence_not_severity() -> None:
    low = finding("R-HEU-01", "x", "new address", NOW, {"priority": "low"})
    assert low.severity is Severity.REVIEW
    assert decide([low]) is Verdict.REVIEW


def test_system_findings_for_required_gaps_only() -> None:
    sources = [
        src("ofac_sdn", SourceStatus.OK),
        src("tron_freeze", SourceStatus.STALE),
        src("tron_blacklist", SourceStatus.ERROR),
        src("bsc_freeze", SourceStatus.SKIPPED),
        src("optional", SourceStatus.ERROR, required=False),
    ]
    out = system_findings(sources, NOW)
    assert [x.evidence["source"] for x in out] == ["tron_blacklist", "tron_freeze"]
    assert all(x.rule_id == "R-SYS-01" and x.severity is Severity.INCOMPLETE for x in out)
    assert out[1].summary == "TRON_FREEZE stale: boom"
    assert decide(out) is Verdict.INCOMPLETE
