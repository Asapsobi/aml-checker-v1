"""BEP20 USDT freeze check: always `skipped`, with the reason (PRD F4.4, D-009, D-039).

The BEP20 USDT contract has no freeze, blacklist, pause or seize function (VS-03), so there is
nothing to check on BSC. Freezes of the same `0x` address on other chains are not checked in v1.
`skipped` is not a gap: a BSC check can end NO_HITS (AT-19), and every BSC result says why.
"""

from __future__ import annotations

from amlcheck.core.clock import Clock, utcnow
from amlcheck.core.models import Address, SourceResult, SourceStatus
from amlcheck.screening.base import SourceHealth

SOURCE = "bsc_freeze"
LABEL = "BEP20 USDT freeze"
REASON = (
    "BEP20 USDT cannot be frozen (its contract has no freeze function); "
    "freezes of this address on other chains are not checked in this version"
)


class BscFreezeSource:
    source = SOURCE
    label = LABEL
    required = False
    timeout: float | None = None

    def __init__(self, *, clock: Clock = utcnow) -> None:
        self._clock = clock

    async def check(self, address: Address) -> SourceResult:
        return SourceResult(
            SOURCE, LABEL, False, SourceStatus.SKIPPED, self._clock(), detail=REASON
        )

    async def health(self) -> SourceHealth:
        return SourceHealth(SOURCE, LABEL, SourceStatus.SKIPPED, None, REASON)
