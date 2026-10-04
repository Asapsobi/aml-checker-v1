"""Amounts for people: two decimals in text, web and PDF; exact in records and JSON (AT-66)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from amlcheck.chain.base import usdt
from amlcheck.cli import runtime
from amlcheck.cli.check import print_result
from amlcheck.core.address import detect
from amlcheck.core.clock import fixed
from amlcheck.core.engine import screen
from amlcheck.report.case_report import gather, render
from amlcheck.report.check_json import check_json
from tests.unit.test_case_report import flat
from tests.unit.test_exposure import ME, NOW, source, tr
from tests.unit.test_web import anon, home, web  # noqa: F401 - fixtures

LONG = "78951.063947843887500723"  # a real BEP20 amount: 18 decimals


@pytest.mark.parametrize(
    ("amount", "shown"),
    [
        (LONG, "78,951.06"),
        ("0", "0.00"),
        ("0.004", "<0.01"),
        ("0.005", "0.01"),
        ("1234567.995", "1,234,568.00"),
        ("100", "100.00"),
    ],
)
def test_usdt(amount: str, shown: str) -> None:
    assert usdt(amount) == shown


# AT-66: shown 78,951.06 in text, the web page and the PDF; exact in the record's JSON.
async def test_at66_two_decimals_for_people_exact_in_json(
    home: Path,  # noqa: F811
    web: TestClient,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
) -> None:
    rt = runtime.load()
    conn = runtime.open_database(rt)
    result = await screen(
        detect(ME),
        [source(conn, [tr(5, "TSENDER", LONG)])],
        conn=conn,
        settings=rt.settings,
        now=fixed(NOW),
    )
    data = check_json(conn, result.check_id)
    assert data is not None
    (exposure,) = data["sources"]
    assert exposure["evidence"]["received_usdt"] == LONG  # exact
    assert exposure["evidence"]["counterparties"][0]["received_usdt"] == LONG
    print_result(result)
    text = capsys.readouterr().out
    assert "received 78,951.06 · sent 0.00 USDT" in text
    assert LONG not in text
    pdf = flat(render(gather(conn, detect(ME))))
    assert "78,951.06" in pdf
    assert LONG not in pdf
    conn.close()
    page = web.get(f"/checks/{result.check_id}").text
    assert "78,951.06" in page
    assert LONG not in page
