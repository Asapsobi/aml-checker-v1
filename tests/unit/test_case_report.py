import base64
import re
import sqlite3
import zlib
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from amlcheck.cli import app, runtime
from amlcheck.config import Settings
from amlcheck.core.address import detect
from amlcheck.core.audit import AuditRecord, append
from amlcheck.core.clock import fixed
from amlcheck.core.engine import screen
from amlcheck.core.models import Address, SourceResult, SourceStatus
from amlcheck.report.case_report import ReportError, gather, render, text
from amlcheck.screening.base import SourceHealth
from amlcheck.storage.db import open_db
from amlcheck.trace.adapter import TraceSource
from amlcheck.trace.jobs import TraceJobs
from tests.unit.test_engine import Fake as Source
from tests.unit.trace_world import NOW, A, Fake, T, engine, example, setup_example

runner = CliRunner()
OTHER = "0x" + "ab" * 20


def pdf_text(pdf: bytes) -> str:
    """The text drawn on the pages: every string shown by Tj, from each decoded page stream."""
    out: list[str] = []
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", pdf, re.S):
        data = m.group(1).strip()
        if data.endswith(b"~>"):
            data = zlib.decompress(base64.a85decode(data[:-2]))
        for s in re.findall(rb"\(((?:\\.|[^\\)])*)\)\s*Tj", data, re.S):
            s = re.sub(rb"\\([0-7]{1,3})", lambda e: bytes([int(e.group(1), 8)]), s)
            s = re.sub(rb"\\([()\\])", rb"\1", s)
            out.append(s.decode("cp1252"))
    return "\n".join(out)


def flat(pdf: bytes) -> str:
    """The page text with line breaks as single spaces: a sentence that carries a random hash wraps
    at a different place each run."""
    return " ".join(pdf_text(pdf).split())


class Stub:
    """A source whose evidence is given: exposure history and classifier types, as recorded."""

    required = True
    timeout: float | None = None

    def __init__(self, source: str, label: str, detail: str, evidence: dict[str, Any]) -> None:
        self.source, self.label, self.detail, self.evidence = source, label, detail, evidence

    async def check(self, address: Address) -> SourceResult:
        return SourceResult(
            self.source, self.label, True, SourceStatus.OK, NOW, (), self.detail, self.evidence
        )

    async def health(self) -> SourceHealth:
        return SourceHealth(self.source, self.label, SourceStatus.OK, None, "test")


EXPOSURE = Stub(
    "exposure",
    "USDT history (exposure)",
    "3 transfer(s) with 3 counterparties since 2026-04-04",
    {
        "since": "2026-04-04T10:00:00Z",
        "transfers": 3,
        "received_usdt": "20000",
        "sent_usdt": "0",
        "first_activity": "2026-09-21T10:00:00Z",
        "zero_value_dropped": 2,
        "counterparty_count": 3,
        "counterparties": [
            {
                "address": A,
                "received_usdt": "12000",
                "sent_usdt": "0",
                "transfers": 1,
                "flags": [],
                "largest": [],
            },
        ],
    },
)
CLASSIFIER = Stub(
    "classifier",
    "Address classifier",
    "PERSONAL (0.6)",
    {"types": [{"type": "PERSONAL", "confidence": "0.6", "primary": True}]},
)


async def checked(conn: sqlite3.Connection, note: str | None = None) -> str:
    eng, store = engine(conn, Fake(example()))
    setup_example(conn, store)
    trace = TraceSource(TraceJobs(conn, Settings(), clock=fixed(NOW)), eng, Settings())
    result = await screen(
        detect(T),
        [trace, EXPOSURE, CLASSIFIER, Source("ofac_sdn"), Source("lookalike", rules=("R-HEU-01",))],
        conn=conn,
        settings=Settings(),
        client="acme",
        note=note,
        amount=None,
        now=fixed(NOW),
    )
    return result.check_id


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / "a.db")


# T-7.05 / F10.4: every section, from stored records only.
async def test_contents(conn: sqlite3.Connection) -> None:
    check_id = await checked(conn)
    data = gather(conn, detect(T))
    assert data.record.check_id == check_id
    assert data.hash_ok
    assert data.trace is not None
    pdf = render(data)
    assert pdf.startswith(b"%PDF-1.4")
    assert pdf.rstrip().endswith(b"%%EOF")
    t = flat(pdf)
    for expected in (
        "Case report",
        T,
        "REVIEW",
        "Review by hand before transacting.",
        "Score 66 · high",
        "E 59.5 · D 0 · B 5 · U 1",
        "R-TRC-01",
        "Funds trace back to a sanctioned wallet 2 hops away",
        "OFAC SDN list",
        "USDT history",
        "2 0-value transfer(s) dropped (address poisoning)",
        A,  # full address in the counterparty table
        "PERSONAL",
        "No decision recorded on this check.",
        "Source of funds",
        "exchange_regulated",
        "60.0%",
        "TARGET",  # the graph's column header
        "HOP 2",
        "0x440000…000000",  # a graph box, 8 + 6 characters
        "matches its contents",
        "INTERNAL USE ONLY",
        "client acme",
    ):
        assert expected in t, expected


async def test_same_records_same_bytes(conn: sqlite3.Connection) -> None:
    await checked(conn)
    first = render(gather(conn, detect(T)))
    assert render(gather(conn, detect(T))) == first


async def test_choose_a_check_and_refusals(conn: sqlite3.Connection) -> None:
    first = await checked(conn)
    await screen(detect(T), [Source("ofac_sdn")], conn=conn, settings=Settings(), now=fixed(NOW))
    assert gather(conn, detect(T)).record.check_id != first  # latest by default
    assert gather(conn, detect(T), first).record.check_id == first
    with pytest.raises(ReportError, match="no check nope"):
        gather(conn, detect(T), "nope")
    with pytest.raises(ReportError, match="is for"):
        gather(conn, detect(OTHER), first)
    with pytest.raises(ReportError, match="never been checked"):
        gather(conn, detect(OTHER))


async def test_record_from_before_scores(conn: sqlite3.Connection) -> None:
    append(
        conn,
        AuditRecord("old", "2026-09-01T00:00:00.000000Z", "bsc", OTHER, "NO_HITS", "0.6.0", 1, "h"),
    )
    data = gather(conn, detect(OTHER))
    assert data.hash_ok
    t = pdf_text(render(data))
    assert "No score: this check was recorded before scores existed" in t
    assert "No trace ran with this check" in t
    assert "None: nothing was found" in t


async def test_tampered_record_is_flagged(conn: sqlite3.Connection) -> None:
    check_id = await checked(conn)
    conn.execute("UPDATE checks SET client = 'other' WHERE check_id = ?", (check_id,))
    data = gather(conn, detect(T))
    assert not data.hash_ok
    assert "DOES NOT MATCH its contents" in flat(render(data))


async def test_text_outside_winansi(conn: sqlite3.Connection) -> None:
    await checked(conn, note="مشتری قدیمی ≥ 10k")  # a Persian note
    t = pdf_text(render(gather(conn, detect(T))))
    assert "note: ????? ????? >= 10k" in t.replace("\n", "")  # may be drawn in two pieces
    assert text("a < b & ≥ →") == "a &lt; b &amp; &gt;= -&gt;"


def test_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("AMLCHECK_HOME", str(home))
    monkeypatch.chdir(tmp_path)
    rt = runtime.load()
    conn = runtime.open_database(rt)
    import asyncio

    asyncio.run(checked(conn))
    conn.close()
    r = runner.invoke(app, ["cp", "report", T])
    assert r.exit_code == 0, r.output
    written = list(tmp_path.glob("case-bsc-*.pdf"))
    assert len(written) == 1
    assert "Case report written to case-bsc-0x54000000-" in r.output
    assert ": REVIEW, score 66 · high" in r.output
    out = tmp_path / "x.pdf"
    assert runner.invoke(app, ["cp", "report", T, "--out", str(out)]).exit_code == 0
    assert out.read_bytes() == written[0].read_bytes()
    missing = runner.invoke(app, ["cp", "report", OTHER])
    assert missing.exit_code == 1
    assert "never been checked" in missing.output


# F10.4 / D-060: the decisions made on the check are in its report.
async def test_decisions_in_the_report(conn: sqlite3.Connection) -> None:
    from amlcheck.cases import cases

    await checked(conn)
    case, _ = cases.open_case(conn, detect(T), by="sobhan", now=NOW)
    gap = pdf_text(render(gather(conn, detect(T))))
    assert "No decision recorded on this check. A case is open (open)." in gap
    cases.decide(conn, case, "escalated", "asked the lead", by="sobhan", now=NOW, tool_version="x")
    cases.decide(conn, case, "rejected", "sanctioned exposure", by="ali", now=NOW, tool_version="x")
    t = flat(render(gather(conn, detect(T))))
    for expected in ("ESCALATED", "asked the lead", "REJECTED", "sanctioned exposure", "ali"):
        assert expected in t, expected
    assert "Decision chain #2, hash" in t
