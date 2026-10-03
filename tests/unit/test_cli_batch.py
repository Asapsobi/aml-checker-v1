import csv
import io
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from amlcheck.cli import app, runtime
from amlcheck.cli.batch import parse
from amlcheck.core.models import Address, Chain
from amlcheck.net.http import Mode
from amlcheck.storage.lock import run_lock
from tests.unit.test_engine import Fake

runner = CliRunner()


def bsc(i: int) -> str:
    return "0x" + format(i + 1, "040x")


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    monkeypatch.setenv("AMLCHECK_HOME", str(h))
    for var in ("AMLCHECK_CONFIG", "AMLCHECK_HYPERSYNC_TOKEN", "AMLCHECK_TRONGRID_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    return h


class Calls:
    def __init__(self) -> None:
        self.addresses: list[str] = []
        self.traced: list[bool] = []


def use(monkeypatch: pytest.MonkeyPatch, calls: Calls, rules: dict[str, tuple[str, ...]]) -> None:
    class Recorder(Fake):
        async def check(self, address: Address) -> Any:
            calls.addresses.append(address.norm)
            self.rules = rules.get(address.norm, ())
            return await super().check(address)

    def sources(
        rt: Any, conn: Any, client: Any, mode: Mode, chain: Chain, *, trace: bool = False
    ) -> list[Fake]:
        assert mode is Mode.CHECK
        calls.traced.append(trace)
        return [Recorder("ofac_sdn")]

    monkeypatch.setattr(runtime, "make_screening_sources", sources)


def write(path: Path, rows: list[str], header: str = "address,chain,amount,client,note") -> Path:
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    return path


def checks(home: Path) -> int:
    if not (home / "amlcheck.db").exists():
        return 0
    return int(
        sqlite3.connect(home / "amlcheck.db").execute("SELECT count(*) FROM checks").fetchone()[0]
    )


# AT-45: 100 rows with one invalid row → nothing screened, exit 1.
def test_at45_one_bad_row_screens_nothing(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = Calls()
    use(monkeypatch, calls, {})
    rows = [f"{bsc(i)},bsc,,acme," for i in range(100)]
    rows[57] = "not-an-address,bsc,,acme,"
    f = write(home.parent / "in.csv", rows)
    r = runner.invoke(app, ["batch", str(f)])
    assert r.exit_code == 1
    assert "line 59:" in r.output  # the header is line 1
    assert "nothing was screened" in r.output
    assert calls.addresses == []
    assert checks(home) == 0


# AT-45, fixed file: every row screened in order, one at a time, results streamed as CSV.
def test_at45_fixed_file_all_screened(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = Calls()
    use(monkeypatch, calls, {bsc(3): ("R-HEU-01",), bsc(7): ("R-SAN-01",)})
    rows = [f"{bsc(i)},bsc,,acme," for i in range(100)]
    f = write(home.parent / "in.csv", rows)
    out = home.parent / "out.csv"
    r = runner.invoke(app, ["batch", str(f), "--out", str(out)])
    assert r.exit_code == 5, r.output  # the most serious verdict: BLOCK
    assert calls.addresses == [bsc(i) for i in range(100)]
    got = list(csv.DictReader(io.StringIO(out.read_text())))
    assert len(got) == 100
    assert list(got[0]) == [
        "row",
        "address",
        "chain",
        "verdict",
        "score",
        "band",
        "rules",
        "check_id",
        "record_hash",
    ]
    assert [g["row"] for g in got[:3]] == ["2", "3", "4"]
    assert (got[3]["verdict"], got[3]["rules"], got[3]["score"], got[3]["band"]) == (
        "REVIEW",
        "R-HEU-01",
        "5",
        "low",
    )
    assert (got[7]["verdict"], got[7]["score"]) == ("BLOCK", "100")
    assert got[0]["verdict"] == "NO_HITS"
    assert checks(home) == 100
    assert "[100/100] line 101: NO_HITS" in r.output  # progress on stderr


def test_stdout_and_exit_codes(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = Calls()
    use(monkeypatch, calls, {bsc(1): ("R-EXP-01",)})
    f = write(home.parent / "in.csv", [bsc(0), bsc(1)], header="address")
    r = runner.invoke(app, ["batch", str(f)])
    assert r.exit_code == 3  # REVIEW is the most serious
    lines = r.stdout.strip().splitlines()
    assert lines[0].startswith("row,address,chain,verdict")
    assert len(lines) == 3
    clean = write(home.parent / "clean.csv", [bsc(0)], header="address")
    assert runner.invoke(app, ["batch", str(clean)]).exit_code == 0


# The trace runs as in `check`: at [trace] auto_amount_usdt or more.
def test_amount_turns_the_trace_on(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = Calls()
    use(monkeypatch, calls, {})
    f = write(home.parent / "in.csv", [f"{bsc(0)},,9999,,", f"{bsc(1)},,10000,,", f"{bsc(2)},,,,"])
    assert runner.invoke(app, ["batch", str(f)]).exit_code == 0
    assert calls.traced == [False, True, False]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("addr\n0x01\n", "must name an `address` column"),
        ("address,colour\nx,red\n", "unknown column(s) colour"),
        ("address,chain\n" + bsc(0) + ",eth\n", "line 2: chain must be tron or bsc"),
        ("address,amount\n" + bsc(0) + ",lots\n", "line 2: amount is not a number"),
        ("address,amount\n" + bsc(0) + ",-5\n", "line 2: amount must be a positive number"),
        ("address\n" + bsc(0) + ",extra\n", "line 2: more values than columns"),
        ("address\n\n", "no rows to screen"),
    ],
)
def test_parse_refusals(text: str, message: str) -> None:
    rows, problems = parse(text)
    assert rows == []
    assert any(message in p for p in problems), problems


def test_parse_rows() -> None:  # blank lines skipped; lines counted from the file
    rows, problems = parse(
        f"address , note\n{bsc(0)},first\n\n TNHrhtVnRMRaTJFRsqLUqSytCKsavXJJaa ,\n"
    )
    assert problems == []
    assert [(r.line, r.address.chain, r.note) for r in rows] == [
        (2, Chain.BSC, "first"),
        (4, Chain.TRON, None),
    ]


def test_one_run_at_a_time(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = Calls()
    use(monkeypatch, calls, {})
    f = write(home.parent / "in.csv", [bsc(0)], header="address")
    with run_lock(home, "test"):
        r = runner.invoke(app, ["batch", str(f)])
    assert r.exit_code == 1
    assert "another batch or watch run is in progress" in r.output
    assert calls.addresses == []
    assert runner.invoke(app, ["batch", str(f)]).exit_code == 0  # released
