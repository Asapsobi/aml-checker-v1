import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from amlcheck.cli import app
from amlcheck.core.audit import AuditRecord, append
from tests.unit.test_cli_check import TRON, home, use  # noqa: F401 - the fixture
from tests.unit.test_engine import Fake

runner = CliRunner()


# T-7.04: the score everywhere the verdict appears.
def test_score_in_check_audit_and_registry(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    use(monkeypatch, Fake("ofac_sdn"), Fake("exposure", rules=("R-EXP-01", "R-HEU-02")))
    r = runner.invoke(app, ["check", TRON])
    assert r.exit_code == 3, r.output
    # R-EXP-01 is REVIEW by itself and its counterparty is an exposure, not points (D-072).
    assert "Score 10 · low  (exposure 0 · behaviour 10)" in r.output
    j = runner.invoke(app, ["check", TRON, "--json"])
    assert json.loads(j.stdout)["score"]["shown"] == "10 · low"
    # A record from before P7 (no score) lists as "-"; a v1 score keeps its v1 band.
    v1 = (
        '{"band":"medium","components":{"B":"5","D":"0","E":"20.4","U":"0"},"hazard":"0.0193",'
        '"lower_bound":false,"score":25,"score_version":1}'
    )
    conn = sqlite3.connect(home / "amlcheck.db")
    for check_id, score_json in (("v1", v1), ("old", None)):
        append(
            conn,
            AuditRecord(
                check_id,
                "2026-09-01T00:00:00.000000Z",
                "tron",
                TRON,
                "REVIEW" if score_json else "NO_HITS",
                "0.6.0",
                1,
                "h",
                score_json=score_json,
            ),
        )
    conn.commit()
    conn.close()
    audit = runner.invoke(app, ["audit", "list"])
    lines = audit.output.splitlines()
    assert "NO_HITS    -" in lines[0]  # appended last, so first (newest seq first)
    assert "REVIEW     25 · medium" in lines[1]  # v1: medium; 25 would be low in v2
    assert "REVIEW     10 · low" in lines[2]
    rows = json.loads(runner.invoke(app, ["audit", "list", "--json"]).stdout)
    assert [x["score"] for x in rows] == [None, 25, 10, 10]
    assert [x["score_shown"] for x in rows] == ["-", "25 · medium", "10 · low", "10 · low"]
    cp = runner.invoke(app, ["cp", "list"])
    assert "REVIEW     10 · low" in cp.output
    show = runner.invoke(app, ["cp", "show", TRON])
    assert "→ REVIEW, score 10 · low" in show.output
    assert json.loads(runner.invoke(app, ["cp", "list", "--json"]).stdout)[0]["last_score"] == 10


def test_incomplete_shows_a_lower_bound(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    from amlcheck.core.models import SourceStatus

    use(monkeypatch, Fake("ofac_sdn", status=SourceStatus.ERROR), Fake("x", rules=("R-HEU-06",)))
    r = runner.invoke(app, ["check", TRON])
    assert r.exit_code == 4, r.output
    assert "Score ≥ 20 · low+" in r.output
    assert "≥ 20 · low+" in runner.invoke(app, ["cp", "list"]).output
