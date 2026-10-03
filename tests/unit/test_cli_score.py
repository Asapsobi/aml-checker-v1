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
    assert "Score 35 · medium  (E 0 · D 25 · B 10 · U 0)" in r.output
    j = runner.invoke(app, ["check", TRON, "--json"])
    assert json.loads(j.stdout)["score"]["shown"] == "35 · medium"
    # A record from before P7 (no score) lists as "-".
    conn = sqlite3.connect(home / "amlcheck.db")
    append(
        conn,
        AuditRecord("old", "2026-09-01T00:00:00.000000Z", "tron", TRON, "NO_HITS", "0.6.0", 1, "h"),
    )
    conn.commit()
    conn.close()
    audit = runner.invoke(app, ["audit", "list"])
    lines = audit.output.splitlines()
    assert "NO_HITS    -" in lines[0]  # appended last, so first (newest seq first)
    assert "REVIEW     35 · medium" in lines[1]
    rows = json.loads(runner.invoke(app, ["audit", "list", "--json"]).stdout)
    assert [x["score"] for x in rows] == [None, 35, 35]
    cp = runner.invoke(app, ["cp", "list"])
    assert "REVIEW     35 · medium" in cp.output
    show = runner.invoke(app, ["cp", "show", TRON])
    assert "→ REVIEW, score 35 · medium" in show.output
    assert json.loads(runner.invoke(app, ["cp", "list", "--json"]).stdout)[0]["last_score"] == 35


def test_incomplete_shows_a_lower_bound(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    from amlcheck.core.models import SourceStatus

    use(monkeypatch, Fake("ofac_sdn", status=SourceStatus.ERROR), Fake("x", rules=("R-HEU-06",)))
    r = runner.invoke(app, ["check", TRON])
    assert r.exit_code == 4, r.output
    assert "Score ≥ 20 · medium+" in r.output
    assert "≥ 20 · medium+" in runner.invoke(app, ["cp", "list"]).output
