"""NBCTF seizure orders imported from the owner's files (methodology §13.1, D-085, AT-71)."""

import sqlite3
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from amlcheck.cli import app
from amlcheck.config import Freshness
from amlcheck.core.address import detect
from amlcheck.core.clock import fixed
from amlcheck.core.models import Severity, SourceStatus
from amlcheck.screening import lists
from amlcheck.screening.sanctions import SanctionsSource
from tests.unit.test_lists import NOW, XINBI
from tests.unit.test_web import home  # noqa: F401 - the fixture

runner = CliRunner()
SECOND = "TA3941uFAvmVibSkQ6fMJXxmaSNovX86mz"
EVM = "0xAbCdEf0123456789aBcDeF0123456789AbCdEf01"
BAD = XINBI[:-1] + ("L" if XINBI[-1] != "L" else "M")


def xlsx(path: Path, *cells: str) -> Path:
    """A spreadsheet as Excel stores it: a zip whose sheet strings are XML."""
    strings = "".join(f"<si><t>{c}</t></si>" for c in cells)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/sharedStrings.xml", f"<sst>{strings}</sst>")
        z.writestr("xl/worksheets/sheet1.xml", "<worksheet><sheetData/></worksheet>")
    return path


# AT-71: TRON and EVM addresses in any cell are imported; a bad checksum is reported, not imported;
# a listed address BLOCKs, naming the order.
def test_at71_import_and_block(home: Path, tmp_path: Path) -> None:  # noqa: F811
    order = xlsx(tmp_path / "ASO 6-26.xlsx", "Wallet", f"USDT TRC20 {XINBI}", BAD, EVM)
    csv = tmp_path / "ASO 7-26.csv"
    csv.write_text(f"name,wallet\nsomeone,{SECOND}\n", encoding="utf-8")
    r = runner.invoke(app, ["lists", "import-nbctf", str(order), str(csv)])
    assert r.exit_code == 0, r.output
    assert "ASO 6-26: 2 address(es); 1 failed their checksum: " + BAD in r.output
    assert "ASO 7-26: 1 address(es)" in r.output
    assert "NBCTF list: 3 address(es) in all" in r.output
    conn = sqlite3.connect(home / "amlcheck.db")
    src = SanctionsSource(
        conn,
        Freshness(),
        clock=fixed(NOW),
        source=lists.NBCTF.source,
        label=lists.NBCTF.label,
        list_name=lists.NBCTF.list_name,
        required=False,
    )
    import asyncio

    result = asyncio.run(src.check(detect(XINBI)))
    assert result.status is SourceStatus.OK
    (f,) = result.findings
    assert (f.rule_id, f.severity) == ("R-SAN-01", Severity.BLOCK)
    assert f.summary.startswith("Listed on the NBCTF list: order ASO 6-26 (entry ASO 6-26")
    stored = {r[0] for r in conn.execute("SELECT address_norm FROM sanctioned_addresses")}
    assert stored == {XINBI, SECOND, EVM.lower()}  # the bad one isn't listed


def test_imports_add_up_unless_replaced(home: Path, tmp_path: Path) -> None:  # noqa: F811
    first = xlsx(tmp_path / "ASO 1.xlsx", XINBI)
    second = xlsx(tmp_path / "ASO 2.xlsx", SECOND)
    assert runner.invoke(app, ["lists", "import-nbctf", str(first)]).exit_code == 0
    r = runner.invoke(app, ["lists", "import-nbctf", str(second)])
    assert "NBCTF list: 2 address(es) in all" in r.output
    r = runner.invoke(app, ["lists", "import-nbctf", str(second), "--replace"])
    assert "NBCTF list: 1 address(es) in all" in r.output


def test_nothing_valid_fails(home: Path, tmp_path: Path) -> None:  # noqa: F811
    empty = tmp_path / "x.txt"
    empty.write_text("no wallets here", encoding="utf-8")
    r = runner.invoke(app, ["lists", "import-nbctf", str(empty)])
    assert r.exit_code == 1
    assert "no valid address" in r.output
    missing = runner.invoke(app, ["lists", "import-nbctf", str(tmp_path / "nope.xlsx")])
    assert missing.exit_code == 1


@pytest.mark.parametrize("name", ["a.xlsx", "a.ods"])
def test_zip_spreadsheets_are_read_as_xml(tmp_path: Path, name: str) -> None:
    path = xlsx(tmp_path / name, XINBI)
    assert lists.read_order(path).addresses == (XINBI,)
