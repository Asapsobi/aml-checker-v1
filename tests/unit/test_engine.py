import asyncio
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from amlcheck.config import Rules, Settings
from amlcheck.core.address import detect
from amlcheck.core.audit import verify
from amlcheck.core.clock import fixed
from amlcheck.core.engine import gaps, screen
from amlcheck.core.models import Address, Finding, SourceResult, SourceStatus, Verdict
from amlcheck.core.rules import finding
from amlcheck.net.http import SourceError
from amlcheck.screening.base import SourceHealth
from amlcheck.storage.db import open_db

NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)
TRON = detect("TNHrhtVnRMRaTJFRsqLUqSytCKsavXJJaa")


@dataclass
class Fake:
    source: str
    status: SourceStatus = SourceStatus.OK
    rules: tuple[str, ...] = ()
    required: bool = True
    raises: Exception | None = None
    delay: float = 0.0
    timeout: float | None = None
    label: str = field(default="")
    calls: int = 0

    async def check(self, address: Address) -> SourceResult:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raises:
            raise self.raises
        fs: tuple[Finding, ...] = tuple(
            finding(r, self.source, r, NOW, {"n": 1}) for r in self.rules
        )
        return SourceResult(
            self.source, self.label or self.source, self.required, self.status, NOW, fs, "d"
        )

    async def health(self) -> SourceHealth:
        return SourceHealth(self.source, self.source, self.status, None, "")


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / "a.db")


async def run(conn: sqlite3.Connection, *sources: Fake, settings: Settings | None = None):  # type: ignore[no-untyped-def]
    return await screen(
        TRON, list(sources), conn=conn, settings=settings or Settings(), now=fixed(NOW)
    )


# AT-09 (engine part): all sources clean → NO_HITS and an audit record written.
async def test_at09_clean_is_no_hits_and_recorded(conn: sqlite3.Connection) -> None:
    r = await run(conn, Fake("ofac_sdn"), Fake("tron_freeze"), Fake("tron_blacklist"))
    assert r.verdict is Verdict.NO_HITS
    assert [s.source for s in r.sources] == ["ofac_sdn", "tron_blacklist", "tron_freeze"]
    row = conn.execute("SELECT check_id, verdict, record_hash FROM checks").fetchone()
    assert row == (r.check_id, "NO_HITS", r.record_hash)
    assert verify(conn).ok


# AT-16: OFAC hit and the TRON freeze index down → BLOCK (precedence), with R-SYS-01 recorded too.
async def test_at16_block_wins_over_a_gap(conn: sqlite3.Connection) -> None:
    r = await run(
        conn,
        Fake("ofac_sdn", rules=("R-SAN-01",)),
        Fake("tron_freeze", raises=SourceError("trongrid", "HTTP 503 after 3 tries")),
    )
    assert r.verdict is Verdict.BLOCK
    assert {f.rule_id for f in r.findings} == {"R-SAN-01", "R-SYS-01"}
    (gap,) = gaps(r)
    assert gap.status is SourceStatus.ERROR
    assert gap.detail == "HTTP 503 after 3 tries"


async def test_stale_required_source_is_incomplete(conn: sqlite3.Connection) -> None:
    r = await run(
        conn,
        Fake("ofac_sdn", status=SourceStatus.STALE),
        Fake("bsc_freeze", status=SourceStatus.SKIPPED, required=False),
    )
    assert r.verdict is Verdict.INCOMPLETE


async def test_skipped_and_optional_failures_are_not_gaps(conn: sqlite3.Connection) -> None:
    r = await run(
        conn,
        Fake("ofac_sdn"),
        Fake("bsc_freeze", status=SourceStatus.SKIPPED, required=False),
        Fake("extra", required=False, raises=RuntimeError("x")),
    )
    assert r.verdict is Verdict.NO_HITS


async def test_bug_in_a_source_becomes_failed_not_a_crash(conn: sqlite3.Connection) -> None:
    r = await run(conn, Fake("ofac_sdn", raises=KeyError("oops")))
    assert r.verdict is Verdict.INCOMPLETE
    assert "internal error (KeyError" in (r.sources[0].detail or "")


async def test_timeout_becomes_failed(conn: sqlite3.Connection) -> None:
    r = await run(conn, Fake("slow", delay=1.0, timeout=0.01))
    assert r.verdict is Verdict.INCOMPLETE
    assert r.sources[0].detail == "no answer within 0.01 s"


async def test_overrides_apply(conn: sqlite3.Connection) -> None:
    s = Settings(rules=Rules(severity={"R-FRZ-02": "BLOCK"}))
    r = await run(conn, Fake("tron_freeze", rules=("R-FRZ-02",)), settings=s)
    assert r.verdict is Verdict.BLOCK


async def test_audit_written_before_return_and_failure_blocks_output(
    conn: sqlite3.Connection,
) -> None:
    conn.execute("DROP TABLE check_findings")  # the append will fail
    with pytest.raises(sqlite3.OperationalError):
        await run(conn, Fake("ofac_sdn", rules=("R-SAN-01",)))
    assert conn.execute("SELECT count(*) FROM checks").fetchone()[0] == 0  # rolled back


async def test_inputs_recorded(conn: sqlite3.Connection) -> None:
    r = await screen(
        TRON,
        [Fake("ofac_sdn")],
        conn=conn,
        settings=Settings(),
        amount=Decimal("50000.00"),
        client="ACME",
        note="pre-trade",
        now=fixed(NOW),
    )
    row = conn.execute(
        "SELECT amount, client, operator_note, rules_version, config_hash FROM checks"
    ).fetchone()
    assert row == ("50000", "ACME", "pre-trade", 1, Settings().hash())
    assert r.amount == Decimal("50000.00")
