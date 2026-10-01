from datetime import UTC, datetime

from amlcheck.core.address import detect
from amlcheck.core.clock import fixed
from amlcheck.core.models import SourceStatus
from amlcheck.core.rules import system_findings
from amlcheck.screening.bsc_freeze import BscFreezeSource


# AT-19: any BSC check → token freeze source skipped with reason; not a gap.
async def test_at19_always_skipped_with_reason() -> None:
    now = datetime(2026, 10, 1, tzinfo=UTC)
    r = await BscFreezeSource(clock=fixed(now)).check(detect("0x" + "ab" * 20))
    assert r.status is SourceStatus.SKIPPED
    assert "cannot be frozen" in (r.detail or "")
    assert "other chains are not checked" in (r.detail or "")
    assert r.findings == ()
    assert system_findings([r], now) == []
