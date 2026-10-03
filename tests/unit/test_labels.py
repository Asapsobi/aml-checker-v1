import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from amlcheck.cli import app
from amlcheck.core.models import Chain
from amlcheck.intel.labels_csv import LabelsError, parse, replace_all, tags_for
from amlcheck.storage.db import open_db

runner = CliRunner()
HEADER = "address,chain,tag,note,source\n"
TRON = "TNHrhtVnRMRaTJFRsqLUqSytCKsavXJJaa"
BSC = "0x55d398326f99059fF775485246999027B3197955"


def test_parse_valid_normalises_and_dedupes() -> None:
    labels = parse(
        HEADER
        + f"{TRON},tron,Mixer,seen in a case,ops\n"
        + f"{BSC},,allowlist,,\n"
        + f"{BSC.lower()},bsc,allowlist,dupe,\n"
        + "\n"
    )
    assert [(x.chain, x.address_norm, x.tag) for x in labels] == [
        (Chain.BSC, BSC.lower(), "allowlist"),
        (Chain.TRON, TRON, "mixer"),
    ]
    assert labels[1].note == "seen in a case"


def test_every_bad_row_reported() -> None:
    with pytest.raises(LabelsError) as e:
        parse(
            HEADER
            + f"{TRON},tron,mixer,,\n"
            + "nonsense,,mixer,,\n"
            + f"{TRON},bsc,mixer,,\n"
            + f"{TRON},solana,mixer,,\n"
            + f"{TRON},,Bad Tag!,,\n"
            + f"{BSC[:-1]}6,,mixer,,\n"
            + f"{TRON},,mixer,,,extra\n"
        )
    lines = [p.split(":")[0] for p in e.value.problems]
    assert lines == ["line 3", "line 4", "line 5", "line 6", "line 7", "line 8"]
    assert "EIP-55" in e.value.problems[4]


def test_header_must_match() -> None:
    with pytest.raises(LabelsError, match="header must be exactly"):
        parse("addr,tag\nx,y\n")


def test_tags_for(tmp_path: Path) -> None:
    conn = open_db(tmp_path / "a.db")
    replace_all(conn, parse(HEADER + f"{TRON},,mixer,,\n{TRON},,high_risk,,\n{BSC},,allowlist,,\n"))
    assert tags_for(conn, Chain.TRON, [TRON, "Tother"]) == {TRON: frozenset({"mixer", "high_risk"})}
    assert tags_for(conn, Chain.BSC, [BSC.lower()]) == {BSC.lower(): frozenset({"allowlist"})}


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    monkeypatch.setenv("AMLCHECK_HOME", str(h))
    monkeypatch.delenv("AMLCHECK_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    return h


# AT-27: `labels import` with one bad row → nothing imported, exit 1, previous labels intact.
def test_at27_bad_row_imports_nothing(home: Path, tmp_path: Path) -> None:
    good = tmp_path / "good.csv"
    good.write_text(HEADER + f"{TRON},tron,mixer,,ops\n")
    r = runner.invoke(app, ["labels", "import", str(good)])
    assert r.exit_code == 0, r.output
    assert "imported 1 label(s)" in r.output
    bad = tmp_path / "bad.csv"
    bad.write_text(HEADER + f"{BSC},bsc,allowlist,,\nnot-an-address,,mixer,,\n")
    r = runner.invoke(app, ["labels", "import", str(bad)])
    assert r.exit_code == 1
    assert "line 3:" in r.output
    assert "nothing imported" in r.output
    rows = (
        sqlite3.connect(home / "amlcheck.db")
        .execute("SELECT address_norm, tag FROM labels")
        .fetchall()
    )
    assert rows == [(TRON, "mixer")]
    listed = json.loads(runner.invoke(app, ["labels", "list", "--json"]).output)
    assert listed == [
        {"chain": "tron", "address": TRON, "tag": "mixer", "note": None, "source": "ops"}
    ]


def test_import_replaces_whole_set(home: Path, tmp_path: Path) -> None:
    a = tmp_path / "a.csv"
    a.write_text(HEADER + f"{TRON},,mixer,,\n")
    runner.invoke(app, ["labels", "import", str(a)])
    b = tmp_path / "b.csv"
    b.write_text(HEADER + f"{BSC},,bridge,,\n")
    runner.invoke(app, ["labels", "import", str(b)])
    r = runner.invoke(app, ["labels", "list"])
    assert "bridge" in r.output
    assert "mixer" not in r.output
    assert "1 label(s)" in r.output


def test_unreadable_file(home: Path, tmp_path: Path) -> None:
    r = runner.invoke(app, ["labels", "import", str(tmp_path / "missing.csv")])
    assert r.exit_code == 1
    assert "can't read" in r.output
