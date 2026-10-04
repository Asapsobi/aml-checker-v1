"""Domain enums and frozen dataclasses shared by every layer.

Imports nothing from the project (architecture §2 dependency direction). Vocabulary from PRD §6–§7.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from amlcheck.core.score import Score


class Chain(StrEnum):
    TRON = "tron"
    BSC = "bsc"


class Verdict(StrEnum):
    """PRD §7.1. Precedence BLOCK > INCOMPLETE > REVIEW > NO_HITS lives in core/verdict.py."""

    BLOCK = "BLOCK"
    INCOMPLETE = "INCOMPLETE"
    REVIEW = "REVIEW"
    NO_HITS = "NO_HITS"


class Severity(StrEnum):
    """What a finding pushes the verdict to.

    Low priority is `"priority": "low"` in evidence, not a severity (CLAUDE.md). `INFO` is shown and
    explains the score but never changes the verdict (methodology §11.5, D-072).
    """

    BLOCK = "BLOCK"
    INCOMPLETE = "INCOMPLETE"
    REVIEW = "REVIEW"
    INFO = "INFO"


class SourceStatus(StrEnum):
    """PRD §6. `skipped` is not a gap; `error` and `stale` on a required source add R-SYS-01."""

    OK = "ok"
    ERROR = "error"
    STALE = "stale"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class Address:
    """A normalised address: TRON base58 `T…`, EVM lowercase `0x…` (PRD F1.2)."""

    chain: Chain
    norm: str
    raw: str


@dataclass(frozen=True)
class Finding:
    rule_id: str
    severity: Severity
    source: str
    summary: str
    observed_at: datetime
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceResult:
    source: str
    label: str
    required: bool
    status: SourceStatus
    observed_at: datetime
    findings: tuple[Finding, ...] = ()
    detail: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CheckResult:
    address: Address
    verdict: Verdict
    checked_at: datetime
    sources: tuple[SourceResult, ...]
    findings: tuple[Finding, ...]
    tool_version: str
    config_hash: str
    check_id: str
    record_hash: str  # this check's audit record (the hash chain), PRD F6
    amount: Decimal | None = None
    client: str | None = None
    note: str | None = None
    score: Score | None = None
