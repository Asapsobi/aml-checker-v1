import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from amlcheck.cli import app
from amlcheck.core.models import Chain
from amlcheck.profile import classifier as clf
from amlcheck.profile.classifier import ClassifyContext
from amlcheck.storage.db import open_db
from tests.unit.test_classifier import NOW, S, collector
from tests.unit.test_cli_check import TRON, use
from tests.unit.test_engine import Fake

runner = CliRunner()


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    monkeypatch.setenv("AMLCHECK_HOME", str(h))
    for var in ("AMLCHECK_CONFIG", "AMLCHECK_HYPERSYNC_TOKEN", "AMLCHECK_TRONGRID_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    return h


def named(home: Path, name: str = "sobhan") -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(f'[operator]\nname = "{name}"\n')


def review(monkeypatch: pytest.MonkeyPatch) -> None:
    use(monkeypatch, Fake("ofac_sdn"), Fake("exposure", rules=("R-HEU-07", "R-EXP-01")))
    assert runner.invoke(app, ["check", TRON, "--client", "acme"]).exit_code == 3


def case_id(output: str) -> str:
    return output.split()[1]


def test_case_flow(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    review(monkeypatch)
    anonymous = runner.invoke(app, ["case", "open", TRON])
    assert anonymous.exit_code == 1
    assert "set [operator] name" in anonymous.output
    named(home)
    r = runner.invoke(app, ["case", "open", TRON])
    assert r.exit_code == 0, r.output
    cid = case_id(r.output)
    assert "opened for tron" in r.output
    assert "already open" in runner.invoke(app, ["case", "open", TRON]).output
    short = cid[:8]
    d1 = runner.invoke(app, ["case", "decide", short, "escalated", "--note", "ask the lead"])
    assert d1.exit_code == 0, d1.output
    assert "escalated by sobhan" in d1.output
    assert "case open" in d1.output
    d2 = runner.invoke(
        app, ["case", "decide", short, "approved", "--note", "invoice matches", "--by", "ali"]
    )
    assert "approved by ali" in d2.output
    assert "case closed" in d2.output
    late = runner.invoke(app, ["case", "decide", short, "rejected", "--note", "x"])
    assert late.exit_code == 1
    assert "is closed" in late.output
    shown_ = runner.invoke(app, ["case", "show", short])
    assert "closed" in shown_.output
    assert "ESCALATED by sobhan: ask the lead" in shown_.output
    assert "APPROVED by ali: invoice matches" in shown_.output
    data = json.loads(runner.invoke(app, ["case", "show", cid, "--json"]).stdout)
    assert [d["decision"] for d in data["decisions"]] == ["escalated", "approved"]
    assert data["check"]["verdict"] == "REVIEW"
    rows = json.loads(runner.invoke(app, ["case", "list", "--json"]).stdout)
    assert [(x["case_id"], x["status"], x["client"]) for x in rows] == [(cid, "closed", "acme")]
    listing = runner.invoke(app, ["case", "list", "--status", "closed"]).output
    assert f"{cid[:8]}  closed" in listing
    assert "approved" in listing
    v = runner.invoke(app, ["audit", "verify"])
    assert v.exit_code == 0
    assert "audit log OK: 1 record(s)" in v.output
    assert "decision log OK: 2 decision(s)" in v.output


# AT-49 through the CLI: the decision chain break is reported; the check chain verifies.
def test_at49_audit_verify_reports_the_decision_chain(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    review(monkeypatch)
    named(home)
    cid = case_id(runner.invoke(app, ["case", "open", TRON]).output)
    runner.invoke(app, ["case", "decide", cid, "rejected", "--note", "mixer exposure"])
    conn = sqlite3.connect(home / "amlcheck.db")
    conn.execute("UPDATE decisions SET decision = 'approved'")
    conn.commit()
    v = runner.invoke(app, ["audit", "verify"])
    assert v.exit_code == 1
    assert "audit log OK: 1 record(s)" in v.output
    assert "decision log broken at decision #1: contents do not match record_hash" in v.output


def test_confirm_reject_and_export(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    review(monkeypatch)
    named(home)
    conn = open_db(home / "amlcheck.db")
    p = collector(address=TRON, distinct_senders=120)
    clf.save(conn, Chain.TRON, p, clf.classify(p, ClassifyContext(), S, NOW), S, NOW)
    conn.close()
    cid = case_id(runner.invoke(app, ["case", "open", TRON]).output)
    missing = runner.invoke(app, ["case", "confirm", cid, "COLLECTOR"])
    assert missing.exit_code == 1
    assert "give --category" in missing.output
    ok = runner.invoke(app, ["case", "confirm", cid, "collector", "--category", "scam"])
    assert ok.exit_code == 0, ok.output
    assert runner.invoke(app, ["case", "reject", cid, "HUB"]).exit_code == 1  # not classified HUB
    runner.invoke(app, ["case", "decide", cid, "rejected", "--note", "scam collector"])
    shown_ = runner.invoke(app, ["case", "show", cid]).output
    assert "confirmed COLLECTOR (classifier v1) (label #1) by sobhan" in shown_
    out = home.parent / "train.jsonl"
    r = runner.invoke(app, ["case", "export", "--jsonl", "--out", str(out)])
    assert r.exit_code == 0
    (line,) = [json.loads(x) for x in out.read_text().splitlines()]
    assert line["decision"]["decision"] == "rejected"
    assert line["decision"]["note"] == "scam collector"
    assert line["case"]["address"] == TRON
    assert line["check"]["verdict"] == "REVIEW"
    assert line["check"]["rules"] == ["R-EXP-01", "R-HEU-07"]
    assert line["check"]["score"]["score"] == 15
    assert line["feedback"] == [
        {"type": "COLLECTOR", "verdict": "confirmed", "classifier_version": 1, "label_id": 1}
    ]
    labels = runner.invoke(app, ["intel", "show", TRON]).output
    assert "scam" in labels
