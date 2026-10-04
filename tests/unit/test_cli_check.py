import json
import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from typer.testing import CliRunner

from amlcheck.cli import app, runtime
from amlcheck.config import Ofac
from amlcheck.core.models import Chain, SourceStatus
from amlcheck.net.http import Mode
from tests.unit.test_engine import Fake

runner = CliRunner()
TRON = "TNHrhtVnRMRaTJFRsqLUqSytCKsavXJJaa"
FIX = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    monkeypatch.setenv("AMLCHECK_HOME", str(h))
    for var in ("AMLCHECK_CONFIG", "AMLCHECK_HYPERSYNC_TOKEN", "AMLCHECK_TRONGRID_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    return h


def use(monkeypatch: pytest.MonkeyPatch, *fakes: Fake) -> None:
    def sources(
        rt: Any, conn: Any, client: Any, mode: Mode, chain: Chain, *, trace: bool = False
    ) -> list[Fake]:
        assert mode is Mode.CHECK
        return list(fakes)

    monkeypatch.setattr(runtime, "make_screening_sources", sources)


def records(home: Path) -> int:
    conn = sqlite3.connect(home / "amlcheck.db")
    return int(conn.execute("SELECT count(*) FROM checks").fetchone()[0])


# AT-07: invalid address → error, no audit record, exit 1.
def test_at07_invalid_address(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use(monkeypatch, Fake("ofac_sdn"))
    r = runner.invoke(app, ["check", "not-an-address"])
    assert r.exit_code == 1
    assert "error:" in r.output
    assert not (home / "amlcheck.db").exists() or records(home) == 0


# AT-08 through the CLI.
def test_at08_bad_checksum(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use(monkeypatch, Fake("ofac_sdn"))
    r = runner.invoke(app, ["check", "0x55d398326f99059ff775485246999027B3197955"])
    assert r.exit_code == 1
    assert "EIP-55" in r.output


# AT-09: valid TRON address, all sources clean → NO_HITS, disclaimer, record written, exit 0.
def test_at09_clean_check(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use(
        monkeypatch,
        Fake("ofac_sdn", label="OFAC SDN list"),
        Fake("tron_freeze"),
        Fake("tron_blacklist"),
    )
    r = runner.invoke(app, ["check", TRON, "--amount", "50,000", "--client", "ACME"])
    assert r.exit_code == 0, r.output
    assert r.output.startswith(f"NO_HITS  ·  TRON {TRON}")
    assert "not a clearance" in r.output
    assert "50,000.00 USDT · client ACME" in r.output
    assert records(home) == 1


@pytest.mark.parametrize(
    ("fakes", "code", "verdict"),
    [
        ((Fake("ofac_sdn", rules=("R-SAN-01",)),), 5, "BLOCK"),
        ((Fake("tron_freeze", rules=("R-FRZ-02",)),), 3, "REVIEW"),
        ((Fake("ofac_sdn", status=SourceStatus.STALE),), 4, "INCOMPLETE"),
    ],
)
def test_exit_codes(
    home: Path, monkeypatch: pytest.MonkeyPatch, fakes: tuple[Fake, ...], code: int, verdict: str
) -> None:
    use(monkeypatch, *fakes)
    r = runner.invoke(app, ["check", TRON, "--json"])
    assert r.exit_code == code, r.output
    out = json.loads(r.output)
    assert out["verdict"] == verdict
    assert out["audit"]["record_hash"]
    assert out["disclaimer"]


def test_bad_amount(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use(monkeypatch, Fake("ofac_sdn"))
    r = runner.invoke(app, ["check", TRON, "--amount", "lots"])
    assert r.exit_code == 1
    assert "--amount" in r.output


# AT-19 through the real BSC sources (no network needed): freeze skipped with the reason; the
# never-downloaded sanctions list keeps it from NO_HITS.
def test_bsc_check_with_real_sources(home: Path) -> None:
    r = runner.invoke(app, ["check", "0x" + "ab" * 20, "--json"])
    assert r.exit_code == 4, r.output
    out = json.loads(r.output)
    by = {s["source"]: s for s in out["sources"]}
    assert by["bsc_freeze"]["status"] == "skipped"
    assert "other chains are not checked" in by["bsc_freeze"]["detail"]
    assert by["ofac_sdn"]["status"] == "stale"
    assert by["exposure"]["status"] == "error"  # no HyperSync token in this test
    assert "AMLCHECK_HYPERSYNC_TOKEN" in by["exposure"]["detail"]
    assert by["trace"]["status"] == "stale"  # every check traces (D-079); it can't read either
    assert [f["rule_id"] for f in out["findings"]] == ["R-SYS-01"] * 3


def test_audit_list_and_verify(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use(monkeypatch, Fake("ofac_sdn", rules=("R-SAN-01",)))
    runner.invoke(app, ["check", TRON, "--client", "acme"])
    use(monkeypatch, Fake("ofac_sdn"))
    runner.invoke(app, ["check", "0x" + "cd" * 20])
    r = runner.invoke(app, ["audit", "list", "--json"])
    rows = json.loads(r.output)
    assert [x["verdict"] for x in rows] == ["NO_HITS", "BLOCK"]
    r = runner.invoke(app, ["audit", "list", "--verdict", "BLOCK", "--client", "ACME", "--json"])
    assert [x["address"] for x in json.loads(r.output)] == [TRON]
    r = runner.invoke(app, ["audit", "list", "--address", TRON, "--since", "1", "--json"])
    assert len(json.loads(r.output)) == 1
    r = runner.invoke(app, ["audit", "verify"])
    assert r.exit_code == 0
    assert "audit log OK: 2 record(s)" in r.output
    conn = sqlite3.connect(home / "amlcheck.db")
    conn.execute("UPDATE checks SET verdict = 'NO_HITS' WHERE seq = 1")
    conn.commit()
    r = runner.invoke(app, ["audit", "verify"])
    assert r.exit_code == 1
    assert "broken at record #1" in r.output


def test_status_shows_sources_and_audit(home: Path) -> None:
    r = runner.invoke(app, ["status", "--json"])
    assert r.exit_code == 0, r.output
    info = json.loads(r.output)
    assert {s["source"]: s["status"] for s in info["sources"]} == {
        "ofac_sdn": "stale",
        "tron_freeze": "stale",
        "bsc_freeze": "skipped",
    }
    assert info["audit"] == {"records": 0, "head_hash": "0" * 64}


@respx.mock
def test_sync_end_to_end_with_fixtures(home: Path) -> None:
    respx.get(Ofac().sdn_url).respond(
        200, content=(FIX / "ofac" / "sdn_sample.xml").read_bytes()
    )  # plain XML also accepted
    tg = FIX / "trongrid"
    respx.post("https://api.trongrid.io/wallet/triggerconstantcontract").respond(
        json=json.loads((tg / "constant_deprecated_false.json").read_text())
    )
    respx.post("https://api.trongrid.io/walletsolidity/getnowblock").respond(
        json=json.loads((tg / "walletsolidity_getnowblock.json").read_text())
    )
    added = json.loads((tg / "events_added_blacklist.json").read_text())
    added["meta"].pop("links")  # one page only
    respx.get(url__regex=r"https://api\.trongrid\.io/v1/contracts/.*/events.*").mock(
        side_effect=lambda req: httpx.Response(
            200,
            json=added
            if req.url.params["event_name"] == "AddedBlackList"
            else {"data": [], "success": True, "meta": {}},
        )
    )
    r = runner.invoke(app, ["sync"])
    assert r.exit_code == 0, r.output
    assert "OFAC SDN list: " in r.output
    assert "addresses (list of 2026-09-30)" in r.output
    assert "Tether TRON freeze index: 3 new events" in r.output
    info = json.loads(runner.invoke(app, ["status", "--json"]).output)
    statuses = {s["source"]: s["status"] for s in info["sources"]}
    assert statuses["ofac_sdn"] == "ok"


# D-079: every check traces unless --no-trace says otherwise.
@pytest.mark.parametrize(
    ("args", "traced"),
    [
        ([], True),
        (["--amount", "9999.99"], True),
        (["--amount", "10000"], True),
        (["--amount", "50000", "--no-trace"], False),
        (["--trace"], True),
    ],
)
def test_trace_switch(
    home: Path, monkeypatch: pytest.MonkeyPatch, args: list[str], traced: bool
) -> None:
    seen: list[bool] = []

    def sources(
        rt: Any, conn: Any, client: Any, mode: Mode, chain: Chain, *, trace: bool = False
    ) -> list[Fake]:
        seen.append(trace)
        return [Fake("ofac_sdn")]

    monkeypatch.setattr(runtime, "make_screening_sources", sources)
    r = runner.invoke(app, ["check", TRON, *args])
    assert r.exit_code == 0, r.output
    assert seen == [traced]
