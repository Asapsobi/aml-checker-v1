import os
import sqlite3
from pathlib import Path
from xml.etree import ElementTree

import pytest

from amlcheck.core.address import detect
from amlcheck.storage.db import open_db
from amlcheck.trace.engine import TraceFailed
from amlcheck.trace.graph import group, render, short
from amlcheck.trace.model import Trace
from tests.unit.trace_world import NOW, A, B, D, E, Fake, T, engine, example, setup_example

NS = "{http://www.w3.org/2000/svg}"
SNAPSHOT = Path(__file__).resolve().parents[1] / "fixtures" / "snapshots" / "trace_example.svg"


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / "a.db")


async def example_trace(conn: sqlite3.Connection) -> Trace:
    eng, store = engine(conn, Fake(example()))
    setup_example(conn, store)
    return await eng.run(detect(T))


# T-6.09: the §7.11 example renders to the stored snapshot, byte for byte.
# Regenerate after an intended change: AMLCHECK_UPDATE_SNAPSHOTS=1 uv run pytest <this file>.
async def test_snapshot(conn: sqlite3.Connection) -> None:
    svg = render(await example_trace(conn))
    if os.environ.get("AMLCHECK_UPDATE_SNAPSHOTS"):
        SNAPSHOT.write_text(svg, encoding="utf-8")
    assert svg == SNAPSHOT.read_text(encoding="utf-8")


async def test_graph_content(conn: sqlite3.Connection) -> None:
    t = await example_trace(conn)
    svg = render(t)
    root = ElementTree.fromstring(svg)  # noqa: S314 - our own output; checks it is well-formed
    assert root.tag == f"{NS}svg"
    assert short(D) in svg
    assert D in svg  # the full address is in the tooltip
    assert "sanctioned · 20.0%" in svg  # legend
    assert "suspicious_collector · 10.0%" in svg
    assert "coverage 90.0%" in svg
    assert f"as of {NOW.isoformat().replace('+00:00', 'Z')}" in svg
    # Edge width grows with the amount: A → T (12,000) is the widest, E → B (2,000) narrower.
    widths = {}
    for path in root.iter(f"{NS}path"):
        title = path.find(f"{NS}title")
        assert title is not None
        assert title.text is not None
        widths[title.text.split(":")[0]] = float(path.get("stroke-width", "0"))
    assert widths[f"{A} → {T}"] == max(widths.values())
    assert widths[f"{E} → {B}"] < widths[f"{B} → {T}"] < widths[f"{A} → {T}"]


def test_short_labels_keep_lookalikes_apart() -> None:
    real = "0x8894e0a0c962cb723c1976a4421c95949be2d4e3"
    fake = "0x8894" + "1" * 32 + "d4e3"
    assert short(real) == "0x8894e0…e2d4e3"
    assert short(real) != short(fake)  # first 4 + last 4 match; 8 + 6 do not
    assert short("TNHrhtVn") == "TNHrhtVn"


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        (None, "expanded"),
        ("untraced:pruned", "untraced"),
        ("sanctioned", "high"),
        ("suspicious_collector", "inferred"),
        ("exchange_nokyc", "elevated"),
        ("otc_desk", "low"),
        ("exchange_regulated", "clean"),
    ],
)
def test_groups(category: str | None, expected: str) -> None:
    assert group(category) == expected


async def test_incomplete_trace_says_so(conn: sqlite3.Connection) -> None:
    eng, store = engine(conn, Fake(example(), fail_on={E}))
    setup_example(conn, store)
    with pytest.raises(TraceFailed) as exc:
        await eng.run(detect(T))
    svg = render(exc.value.partial)
    assert "INCOMPLETE" in svg
    ElementTree.fromstring(svg)  # noqa: S314 - our own output
