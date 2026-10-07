"""NBCTF seizure orders imported from the owner's files (methodology §13.1, D-085, AT-71, VS-20)."""

import asyncio
import csv
import io
import sqlite3
import zipfile
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from amlcheck.cli import app, runtime
from amlcheck.config import Freshness
from amlcheck.core.address import detect
from amlcheck.core.clock import fixed
from amlcheck.core.models import Severity, SourceStatus
from amlcheck.screening import lists
from amlcheck.screening.sanctions import SanctionsSource
from tests.unit.test_lists import FIX, NOW, XINBI
from tests.unit.test_web import home  # noqa: F401 - the fixture

runner = CliRunner()
SECOND = "TA3941uFAvmVibSkQ6fMJXxmaSNovX86mz"
EVM = "0xAbCdEf0123456789aBcDeF0123456789AbCdEf01"
BAD = XINBI[:-1] + ("L" if XINBI[-1] != "L" else "M")
#: Five real orders of the official export (VS-20), trimmed; the people columns emptied.
EXPORT = FIX / "nbctf_orders_sample.csv"
ASO_06_26 = (
    "TB5UPBTtXYwwmSviWBkQcFM64o6R1CDXUY",
    "TGtpnHgW8gcfzRj1NEkLXR9AbN8WWLdJ2p",
    "TP834zaucGKtAVjJPc9QQQkz5ses87KEf8",
)
LOOK_ALIKE = "TН96tFMn8KGiYSLiwсV3E2UiaJc8jmcbz3"  # FO 02/24: a Cyrillic Н and с
LATIN = "TH96tFMn8KGiYSLiwcV3E2UiaJc8jmcbz3"


@pytest.fixture
def at_now(home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:  # noqa: F811
    """The CLI's clock fixed at NOW, so validity dates are judged the same every day."""
    real_load = runtime.load

    def load() -> Any:
        rt = real_load()
        return runtime.Runtime(rt.paths, rt.settings, rt.secrets, clock=lambda: NOW)

    monkeypatch.setattr(runtime, "load", load)
    return home


def xlsx(path: Path, *cells: str) -> Path:
    """A spreadsheet as Excel stores it: a zip whose sheet strings are XML."""
    strings = "".join(f"<si><t>{c}</t></si>" for c in cells)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/sharedStrings.xml", f"<sst>{strings}</sst>")
        z.writestr("xl/worksheets/sheet1.xml", "<worksheet><sheetData/></worksheet>")
    return path


def nbctf(conn: sqlite3.Connection) -> SanctionsSource:
    return SanctionsSource(
        conn,
        Freshness(),
        clock=fixed(NOW),
        source=lists.NBCTF.source,
        label=lists.NBCTF.label,
        list_name=lists.NBCTF.list_name,
        required=False,
    )


def listed(home: Path) -> dict[str, set[str]]:  # noqa: F811
    """Entry → addresses in the latest `nbctf` snapshot."""
    conn = sqlite3.connect(home / "amlcheck.db")
    out: dict[str, set[str]] = {}
    for entry, a in conn.execute(
        "SELECT list_entry_id, address_norm FROM sanctioned_addresses WHERE snapshot_id = "
        "(SELECT max(id) FROM list_snapshots WHERE source = 'nbctf')"
    ):
        out.setdefault(entry, set()).add(a)
    return out


def edited(tmp_path: Path, name: str, change: dict[str, str], drop: str | None = None) -> Path:
    """The fixture with one order's cells changed and another order left out."""
    rows = list(csv.DictReader(io.StringIO(EXPORT.read_text(encoding="utf-8-sig"))))
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=list(rows[0]), lineterminator="\n")
    w.writeheader()
    for row in rows:
        if row["Name en"] == drop:
            continue
        w.writerow({**row, **change} if row["Name en"] == name else row)
    path = tmp_path / "export.csv"
    path.write_text(out.getvalue(), encoding="utf-8-sig")
    return path


# AT-71: TRON and EVM addresses in any cell are imported; a bad checksum is reported, not imported;
# a listed address BLOCKs, naming the order.
def test_at71_import_and_block(home: Path, tmp_path: Path) -> None:  # noqa: F811
    order = xlsx(tmp_path / "ASO 6-26.xlsx", "Wallet", f"USDT TRC20 {XINBI}", BAD, EVM)
    csv_file = tmp_path / "ASO 7-26.csv"
    csv_file.write_text(f"name,wallet\nsomeone,{SECOND}\n", encoding="utf-8")
    r = runner.invoke(app, ["lists", "import-nbctf", str(order), str(csv_file)])
    assert r.exit_code == 0, r.output
    assert "ASO 6-26: 2 address(es); 1 failed their checksum: " + BAD in r.output
    assert "ASO 7-26: 1 address(es)" in r.output
    assert "NBCTF list: 3 address(es) in all" in r.output
    conn = sqlite3.connect(home / "amlcheck.db")
    result = asyncio.run(nbctf(conn).check(detect(XINBI)))
    assert result.status is SourceStatus.OK
    (f,) = result.findings
    assert (f.rule_id, f.severity) == ("R-SAN-01", Severity.BLOCK)
    assert f.summary.startswith("Listed on the NBCTF list: order ASO 6-26 (entry ASO 6-26")
    stored = {r[0] for r in conn.execute("SELECT address_norm FROM sanctioned_addresses")}
    assert stored == {XINBI, SECOND, EVM.lower()}  # the bad one isn't listed


# AT-71 (official export, VS-20): one order per row, its type and dates in the finding; a TRON
# address typed with look-alike letters is read when its checksum then holds; an order without a
# wallet (bank accounts, phone numbers) lists nothing; a passed validity date is shown, still
# listed (Q-38).
def test_at71_official_export(at_now: Path) -> None:
    r = runner.invoke(app, ["lists", "import-nbctf", str(EXPORT)])
    assert r.exit_code == 0, r.output
    assert r.output.splitlines() == [
        "nbctf_orders_sample.csv: 5 order(s) in the official export, 4 with TRON or 0x addresses",
        "  ASO 06/26: 3 address(es)",
        "  FO 50/25: 1 address(es)",
        "  FO 02/24: 3 address(es); validity date 2026-07-31 has passed; still listed (Q-38)",
        "  FO 19/23: 3 address(es)",
        "NBCTF list: 10 address(es) in all; they BLOCK from now on",
    ]
    assert listed(at_now) == {
        "ASO 06/26": set(ASO_06_26),
        "FO 50/25": {"TSoQQCZ3DrZ1us6RcHGHd9UXp6bM6PukNQ"},
        "FO 02/24": {
            LATIN,
            "0x21b8d56bda776bbe68655a16895afd96f5534fed",
            "0x175d44451403edf28469df03a9280c1197adb92c",
        },
        "FO 19/23": {  # one cell held "0xc8fe…, TR2n…"
            "TRwvSmistRy7vK8mq2xpPA85fj9wRJKcUB",
            "TR2nTb64cQMx6tqFwisoC6o7barFWHhPiw",
            "0xc8fe1c81e927540fcc99ebb3c880a840082293da",
        },
    }
    conn = sqlite3.connect(at_now / "amlcheck.db")
    (f,) = asyncio.run(nbctf(conn).check(detect(ASO_06_26[0]))).findings
    assert (f.rule_id, f.severity) == ("R-SAN-01", Severity.BLOCK)
    assert f.summary == (
        "Listed on the NBCTF list: order ASO 06/26 "
        "(entry ASO 06/26, ASO (Seizure) of 2026-02-16, valid to 2028-02-16)"
    )


def test_an_orders_newest_import_wins(at_now: Path, tmp_path: Path) -> None:
    """Re-importing the export: a cancelled order is no longer listed; an order the new file
    leaves out stays listed (imports add up), unless --replace."""
    assert runner.invoke(app, ["lists", "import-nbctf", str(EXPORT)]).exit_code == 0
    newer = edited(tmp_path, "ASO 06/26", {"Is Canceled": "True"}, drop="FO 50/25")
    r = runner.invoke(app, ["lists", "import-nbctf", str(newer)])
    assert r.exit_code == 0, r.output
    assert "  ASO 06/26: cancelled; not listed" in r.output
    assert "NBCTF list: 7 address(es) in all" in r.output
    assert set(listed(at_now)) == {"FO 50/25", "FO 02/24", "FO 19/23"}
    r = runner.invoke(app, ["lists", "import-nbctf", str(newer), "--replace"])
    assert set(listed(at_now)) == {"FO 02/24", "FO 19/23"}


def test_the_export_zip_is_read(tmp_path: Path) -> None:
    """Several lists exported at once come as a zip of CSVs; only the order lists hold wallets."""
    path = tmp_path / "export.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("organization.csv", "﻿Id,Name en,Phone Numbers\nx,Someone,972500000000\n")
        z.writestr("seizureAndForfeitureOrderCryptocurrency.csv", EXPORT.read_bytes())
    f = lists.read_file(path)
    assert f.official
    assert [o.order for o in f.orders] == [
        "ASO 06/26",
        "FO 50/25",
        "FO 02/24",
        "FO 19/23",
        "FO 15/23",
    ]
    assert f.orders[0].addresses == ASO_06_26


def test_look_alike_letters(tmp_path: Path) -> None:
    """Read as Latin only when the TRON checksum then holds; one that fails is reported as
    written."""
    wrong = LOOK_ALIKE[:-1] + "4"
    text = tmp_path / "order.txt"
    text.write_text(f"{LOOK_ALIKE}\n{wrong}\n0x21B8d56BDA776bbE68655A16895afd96F5534fеD\n", "utf-8")
    (o,) = lists.read_file(text).orders
    assert o.addresses == (LATIN,)  # the `0x` one has a Cyrillic е: no checksum, not read
    assert o.rejected == (wrong,)
    assert lists.addresses_in(f"кошелёк {LOOK_ALIKE}") == [LATIN]


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
    f = lists.read_file(xlsx(tmp_path / name, XINBI))
    assert (f.official, [o.addresses for o in f.orders]) == (False, [(XINBI,)])
