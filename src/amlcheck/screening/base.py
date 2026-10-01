"""SourceAdapter protocol and `failed()` (architecture §3).

Every source answers with a `SourceResult`; a provider failure becomes `failed(...)`, never an
exception out of the engine (CLAUDE.md). The engine turns a required source in `error` or `stale`
into R-SYS-01.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from amlcheck.core.models import Address, SourceResult, SourceStatus


@dataclass(frozen=True)
class SourceHealth:
    source: str
    label: str
    status: SourceStatus
    as_of: datetime | None
    detail: str


class SourceAdapter(Protocol):
    source: str  # stable id, e.g. "ofac_sdn"
    label: str  # shown to the operator
    required: bool  # failure → R-SYS-01
    timeout: float | None  # None = engine default

    async def check(self, address: Address) -> SourceResult: ...

    async def health(self) -> SourceHealth: ...


def failed(
    adapter: SourceAdapter,
    now: datetime,
    detail: str,
    status: SourceStatus = SourceStatus.ERROR,
) -> SourceResult:
    return SourceResult(
        source=adapter.source,
        label=adapter.label,
        required=adapter.required,
        status=status,
        observed_at=now,
        detail=detail,
    )
