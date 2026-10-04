import dataclasses
from datetime import UTC, datetime

import pytest

from amlcheck.core.models import Address, Chain, Finding, Severity, SourceStatus, Verdict


def test_enum_values_match_storage_vocabulary() -> None:
    # Data model 0002: check_sources.status CHECK constraint.
    assert {s.value for s in SourceStatus} == {"ok", "error", "stale", "skipped"}
    assert {v.value for v in Verdict} == {"BLOCK", "INCOMPLETE", "REVIEW", "NO_HITS"}
    assert {c.value for c in Chain} == {"tron", "bsc"}
    # INFO never becomes a verdict (methodology §11.5); the others are verdicts too.
    assert {s.value for s in Severity} - {"INFO"} <= {v.value for v in Verdict}


def test_dataclasses_are_frozen() -> None:
    a = Address(Chain.BSC, "0xabc", "0xABC")
    with pytest.raises(dataclasses.FrozenInstanceError):
        a.norm = "x"  # type: ignore[misc]
    f = Finding("R-SAN-01", Severity.BLOCK, "ofac_sdn", "listed", datetime(2026, 1, 1, tzinfo=UTC))
    assert f.evidence == {}
