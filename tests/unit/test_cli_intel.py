import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from amlcheck.cli import app
from tests.unit.test_cli_check import TRON, use
from tests.unit.test_engine import Fake

runner = CliRunner()
BSC = "0x" + "ab" * 20


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    monkeypatch.setenv("AMLCHECK_HOME", str(h))
    for var in ("AMLCHECK_CONFIG", "AMLCHECK_HYPERSYNC_TOKEN", "AMLCHECK_TRONGRID_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    return h


# AT-31 through the CLI.
def test_label_retract_show(home: Path) -> None:
    r = runner.invoke(app, ["intel", "label", TRON, "mixer", "--note", "case 7", "--by", "sobhan"])
    assert r.exit_code == 0, r.output
    assert "label #1" in r.output
    assert "decisive category: mixer" in runner.invoke(app, ["intel", "show", TRON]).output
    r = runner.invoke(app, ["intel", "retract", "1", "--reason", "wrong address"])
    assert r.exit_code == 0, r.output
    assert "no labels" in runner.invoke(app, ["intel", "show", TRON]).output
    shown = json.loads(runner.invoke(app, ["intel", "show", TRON, "--all", "--json"]).output)
    (x,) = shown["labels"]
    assert (x["category"], x["retract_reason"], x["created_by"]) == (
        "mixer",
        "wrong address",
        "sobhan",
    )


def test_label_refusals(home: Path) -> None:
    r = runner.invoke(app, ["intel", "label", TRON, "sanctioned"])
    assert r.exit_code == 1
    assert "only come from: list" in r.output
    r = runner.invoke(app, ["intel", "retract", "9", "--reason", "x"])
    assert r.exit_code == 1


# AT-30 through the CLI.
def test_import_pack_needs_licence(home: Path, tmp_path: Path) -> None:
    pack = tmp_path / "pack.csv"
    pack.write_text(f"address,chain,category,note\n{TRON},tron,scam,\n")
    r = runner.invoke(app, ["intel", "import-pack", str(pack), "--name", "acme"])
    assert r.exit_code == 1
    assert "--licence" in r.output
    assert "nothing imported" in r.output
    assert (
        json.loads(runner.invoke(app, ["intel", "stats", "--json"]).output)["active_labels"] == {}
    )
    r = runner.invoke(
        app, ["intel", "import-pack", str(pack), "--name", "acme", "--licence", "ACME EULA"]
    )
    assert r.exit_code == 0, r.output
    assert "1 label(s) imported" in r.output


def test_cp_after_checks_and_rebuild(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    use(monkeypatch, Fake("ofac_sdn"))
    for addr, client in [(TRON, "acme"), (BSC, None), (TRON, "beta")]:
        runner.invoke(app, ["check", addr, "--client", client] if client else ["check", addr])
    rows = json.loads(runner.invoke(app, ["cp", "list", "--json"]).output)
    assert {r["address_norm"]: r["check_count"] for r in rows} == {TRON: 2, BSC: 1}
    acme = json.loads(runner.invoke(app, ["cp", "list", "--client", "ACME", "--json"]).output)
    assert [r["address_norm"] for r in acme] == [TRON]
    shown = json.loads(runner.invoke(app, ["cp", "show", TRON, "--json"]).output)
    assert shown["registry"]["clients"] == ["acme", "beta"]
    r = runner.invoke(app, ["cp", "rebuild"])
    assert "rebuilt from 3 audit record(s): 2 counterparties" in r.output
    assert json.loads(runner.invoke(app, ["cp", "list", "--json"]).output) == rows
    assert "not in the registry" in runner.invoke(app, ["cp", "show", "0x" + "cd" * 20]).output


def test_prune_keeps_counterparties_and_labelled(home: Path) -> None:
    import sqlite3

    runner.invoke(app, ["status"])  # creates the DB
    conn = sqlite3.connect(home / "amlcheck.db")
    for addr in ("Tkept_label", "Tgone"):
        conn.execute(
            "INSERT INTO history_windows VALUES ('tron', ?, '2020-01-01T00:00:00.000000Z', "
            "'2020-02-01T00:00:00.000000Z', 1, 0, 0, NULL, 't', '2020-02-01T00:00:00.000000Z')",
            (addr,),
        )
    conn.execute("INSERT INTO labels VALUES ('tron', 'Tkept_label', 'vip', NULL, NULL)")
    conn.commit()
    r = runner.invoke(app, ["cache", "prune", "--days", "30"])
    assert "forgot 1 address(es)" in r.output
    left = [x[0] for x in conn.execute("SELECT address_norm FROM history_windows")]
    assert left == ["Tkept_label"]
