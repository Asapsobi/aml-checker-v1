import csv
import hashlib
import io
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from amlcheck.cli import app
from amlcheck.core.audit import GENESIS, canonical_json
from amlcheck.report.export import csv_cell
from tests.unit.test_case_report import pdf_text
from tests.unit.test_cli_check import TRON, use
from tests.unit.test_engine import Fake

runner = CliRunner()
BSC = "0x" + "34" * 20


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    monkeypatch.setenv("AMLCHECK_HOME", str(h))
    for var in ("AMLCHECK_CONFIG", "AMLCHECK_HYPERSYNC_TOKEN", "AMLCHECK_TRONGRID_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    return h


def record_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    use(monkeypatch, Fake("ofac_sdn"), Fake("exposure", rules=("R-HEU-02",)))
    assert runner.invoke(app, ["check", TRON, "--client", '=HYPERLINK("http://x")']).exit_code == 3
    assert (
        runner.invoke(app, ["check", BSC, "--client", "acme", "--note", "+1 from ops"]).exit_code
        == 3
    )
    use(monkeypatch, Fake("ofac_sdn"))
    assert runner.invoke(app, ["check", BSC, "--client", "acme"]).exit_code == 0


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("=1+1", "'=1+1"),
        ("+31 6", "'+31 6"),
        ("-5", "'-5"),
        ("@SUM(A1)", "'@SUM(A1)"),
        ("\tx", "'\tx"),
        ("\rx", "'\rx"),
        ("acme", "acme"),
        ("a=b", "a=b"),
        (None, ""),
        (66, "66"),
    ],
)
def test_csv_cell(value: object, expected: str) -> None:
    assert csv_cell(value) == expected


# AT-47: a cell starting with `=` is prefixed with `'`.
def test_at47_csv_export_is_spreadsheet_safe(
    home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record_checks(monkeypatch)
    r = runner.invoke(app, ["audit", "export"])
    assert r.exit_code == 0, r.output
    rows = list(csv.DictReader(io.StringIO(r.stdout)))
    assert [x["seq"] for x in rows] == ["1", "2", "3"]  # oldest first
    assert rows[0]["client"] == '\'=HYPERLINK("http://x")'
    assert rows[1]["note"] == "'+1 from ops"
    assert (rows[1]["verdict"], rows[1]["score"], rows[1]["band"], rows[1]["rules"]) == (
        "REVIEW",
        "10",
        "low",
        "R-HEU-02",
    )
    assert rows[2]["rules"] == ""


def test_filters(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record_checks(monkeypatch)

    def seqs(*args: str) -> list[str]:
        out = runner.invoke(app, ["audit", "export", *args]).stdout
        return [x["seq"] for x in csv.DictReader(io.StringIO(out))]

    assert seqs("--address", BSC) == ["2", "3"]
    assert seqs("--verdict", "REVIEW") == ["1", "2"]
    assert seqs("--client", "ACME") == ["2", "3"]
    assert seqs("--since", "2000-01-01") == ["1", "2", "3"]
    assert seqs("--until", "2000-01-01") == []


# The JSON export carries every hash: the chain re-verifies from the file alone.
def test_json_export_reverifies(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record_checks(monkeypatch)
    out = home.parent / "audit.json"
    r = runner.invoke(app, ["audit", "export", "--format", "json", "--out", str(out)])
    assert r.exit_code == 0
    assert "3 check(s) exported" in r.output
    items = json.loads(out.read_text(encoding="utf-8"))
    prev = GENESIS
    for item in items:
        assert item["prev_hash"] == prev
        digest = hashlib.sha256((prev + canonical_json(item["record"])).encode()).hexdigest()
        assert digest == item["record_hash"]
        prev = digest
    assert (
        items[0]["record"]["check"]["client"] == '=HYPERLINK("http://x")'
    )  # JSON keeps data as is
    assert items[1]["record"]["findings"][0]["rule_id"] == "R-HEU-02"


def test_pdf_export(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record_checks(monkeypatch)
    refused = runner.invoke(app, ["audit", "export", "--format", "pdf"])
    assert refused.exit_code == 1
    assert "needs --out" in refused.output
    out = home.parent / "audit.pdf"
    assert (
        runner.invoke(app, ["audit", "export", "--format", "pdf", "--out", str(out)]).exit_code == 0
    )
    pdf = out.read_bytes()
    assert pdf.startswith(b"%PDF-")
    t = pdf_text(pdf)
    for expected in (
        "Audit export",
        "3 check(s)",
        TRON,
        BSC,
        "10 · low",
        "R-HEU-02",
        "INTERNAL USE ONLY",
    ):
        assert expected in t, expected
    again = home.parent / "again.pdf"
    runner.invoke(app, ["audit", "export", "--format", "pdf", "--out", str(again)])
    assert again.read_bytes() == pdf  # same records, same bytes
