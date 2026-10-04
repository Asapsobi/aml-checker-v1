import json
import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from amlcheck.cli import app, runtime
from amlcheck.config import Settings
from amlcheck.core.models import Chain
from amlcheck.net.http import Mode
from amlcheck.trace.adapter import TraceSource
from amlcheck.trace.engine import TraceEngine
from amlcheck.trace.jobs import TraceJobs
from tests.unit.test_engine import Fake as FakeSource
from tests.unit.trace_world import NOW, E, Fake, T, engine, example, setup_example

runner = CliRunner()


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    monkeypatch.setenv("AMLCHECK_HOME", str(h))
    for var in ("AMLCHECK_CONFIG", "AMLCHECK_HYPERSYNC_TOKEN", "AMLCHECK_TRONGRID_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    return h


def world(conn: sqlite3.Connection, fail: bool) -> TraceEngine:
    eng, store = engine(conn, Fake(example(), fail_on={E} if fail else set()))
    setup_example(conn, store)
    return eng


def use_world(monkeypatch: pytest.MonkeyPatch, *, fail: bool = False) -> None:
    def build(rt: Any, conn: sqlite3.Connection, client: Any, mode: Mode) -> TraceEngine:
        assert mode is Mode.BACKGROUND
        return world(conn, fail)

    def sources(
        rt: Any, conn: sqlite3.Connection, client: Any, mode: Mode, chain: Chain, *, trace: bool
    ) -> list[Any]:
        assert trace
        jobs = TraceJobs(conn, rt.settings, clock=rt.clock)
        return [FakeSource("ofac_sdn"), TraceSource(jobs, world(conn, fail), Settings())]

    monkeypatch.setattr(runtime, "build_trace_engine", build)
    monkeypatch.setattr(runtime, "make_screening_sources", sources)


def db(home: Path) -> sqlite3.Connection:
    return sqlite3.connect(home / "amlcheck.db")


# T-6.10: `trace` shows the exposure table, coverage, top paths and budget; no check record.
def test_trace_human(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use_world(monkeypatch)
    r = runner.invoke(app, ["trace", T])
    assert r.exit_code == 0, r.output
    out = r.output
    assert "Source of funds  ·  BSC " + T in out
    assert "20,000.00 USDT received in the window · coverage 90.0%" in out
    assert "exchange_regulated" in out
    assert "60.0%" in out
    assert "12,000.00" in out  # ≈ USDT for 60% of 20,000
    assert "Top paths" in out
    assert "R-TRC-01" in out
    assert "every hop on the path moved at least 4,000.00 USDT" in out  # the bottleneck, §7.3
    assert "Budget  3 address(es) read" in out
    assert "INCOMPLETE" not in out
    conn = db(home)
    assert conn.execute("SELECT count(*) FROM checks").fetchone()[0] == 0
    row = conn.execute("SELECT status, requested_by FROM traces").fetchone()
    assert row == ("done", "cli")


def test_trace_json_and_svg(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use_world(monkeypatch)
    svg = home.parent / "graph.svg"
    r = runner.invoke(app, ["trace", T, "--json", "--svg", str(svg)])
    assert r.exit_code == 0, r.output
    data = json.loads(r.stdout)
    assert data["complete"] is True
    assert data["partition"]["sanctioned"] == "0.2"
    assert data["coverage"] == "0.9"
    assert {f["rule_id"] for f in data["findings"]} == {"R-TRC-01", "R-TRC-03", "R-TRC-05"}
    stored = db(home).execute("SELECT trace_id FROM traces").fetchone()[0]
    assert data["trace_id"] == stored
    assert svg.read_text(encoding="utf-8").startswith("<svg ")


# AT-39 through the CLI: partial trace shown, exit 4, no findings from a partial.
def test_trace_incomplete(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use_world(monkeypatch, fail=True)
    r = runner.invoke(app, ["trace", T])
    assert r.exit_code == 4, r.output
    assert "INCOMPLETE:" in r.output
    assert E in r.output
    assert "sanctioned" in r.output  # what was decided before the failure
    assert "Trace findings" not in r.output
    assert db(home).execute("SELECT status FROM traces").fetchone()[0] == "failed"


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["trace", T, "--direction", "sideways"], "--direction must be in or out"),
        (["trace", "not-an-address"], "error:"),
    ],
)
def test_trace_bad_input(
    home: Path, monkeypatch: pytest.MonkeyPatch, args: list[str], message: str
) -> None:
    use_world(monkeypatch)
    r = runner.invoke(app, args)
    assert r.exit_code == 1
    assert message in r.output


# D-049: investigate = a check with the trace on; one audit record linked to the trace.
def test_investigate(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use_world(monkeypatch)
    svg = home.parent / "case.svg"
    r = runner.invoke(app, ["investigate", T, "--client", "acme", "--svg", str(svg)])
    assert r.exit_code == 3, r.output  # REVIEW (methodology §7.11)
    assert r.output.startswith("REVIEW")
    assert "R-TRC-01" in r.output
    assert "Source of funds  ·  BSC " + T in r.output
    assert "Destination of funds  ·  BSC " + T in r.output  # both ways (D-079)
    assert svg.exists()
    conn = db(home)
    checks = conn.execute("SELECT trace_id, client FROM checks").fetchall()
    traces = dict(
        conn.execute("SELECT direction, trace_id FROM traces WHERE requested_by = 'check'")
    )
    assert len(checks) == 1
    assert set(traces) == {"in", "out"}  # both ways (D-079)
    assert checks[0] == (traces["in"], "acme")  # the check carries the inbound trace's id


def test_investigate_json(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use_world(monkeypatch)
    r = runner.invoke(app, ["investigate", T, "--json"])
    assert r.exit_code == 3, r.output
    data = json.loads(r.stdout)
    assert data["verdict"] == "REVIEW"
    trace = data["trace"]
    source = next(s for s in data["sources"] if s["source"] == "trace")
    assert trace["trace_id"] == source["evidence"]["trace_id"]
    assert trace["partition"]["exchange_regulated"] == "0.6"
    assert trace["as_of"] == NOW.isoformat().replace("+00:00", "Z")


def test_investigate_incomplete(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use_world(monkeypatch, fail=True)
    r = runner.invoke(app, ["investigate", T])
    assert r.exit_code == 4, r.output
    assert r.output.startswith("INCOMPLETE")
    assert "R-SYS-01" in r.output
    assert "Source of funds" in r.output  # the partial trace is still shown


# The trace in a check gets its own client in background mode (D-036) and its own query count.
@pytest.mark.parametrize("chain", [Chain.TRON, Chain.BSC])
async def test_check_wiring(home: Path, monkeypatch: pytest.MonkeyPatch, chain: Chain) -> None:
    modes: list[Mode] = []
    real = runtime.build_trace_engine

    def build(rt: Any, conn: sqlite3.Connection, client: Any, mode: Mode) -> TraceEngine:
        modes.append(mode)
        return real(rt, conn, client, mode)

    monkeypatch.setattr(runtime, "build_trace_engine", build)
    rt = runtime.load()
    conn = runtime.open_database(rt)
    try:
        async with httpx.AsyncClient() as client:
            plain = runtime.make_screening_sources(rt, conn, client, Mode.CHECK, chain)
            traced = runtime.make_screening_sources(rt, conn, client, Mode.CHECK, chain, trace=True)
    finally:
        conn.close()
    assert not any(isinstance(s, TraceSource) for s in plain)
    assert isinstance(traced[-1], TraceSource)
    assert [s.source for s in traced[:-1]] == [s.source for s in plain]
    assert modes == [Mode.BACKGROUND] * 2  # one engine per direction, each its own client
