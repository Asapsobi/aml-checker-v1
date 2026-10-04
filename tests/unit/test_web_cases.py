import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from amlcheck.cli import runtime
from amlcheck.core.models import Chain
from amlcheck.profile import classifier as clf
from amlcheck.profile.classifier import ClassifyContext
from amlcheck.web.app import create_app
from tests.unit.test_classifier import NOW, S, collector
from tests.unit.test_web import BASE, TOKEN, TRON, World

OTHER = "0x" + "45" * 20


def client(home: Path, name: str | None) -> TestClient:
    home.mkdir(parents=True, exist_ok=True)
    if name:
        (home / "config.toml").write_text(f'[operator]\nname = "{name}"\n')
    rt = runtime.load()
    runtime.open_database(rt).close()
    c = TestClient(create_app(rt, TOKEN), base_url=BASE, follow_redirects=False)
    assert c.get(f"/login?t={TOKEN}").status_code == 303
    return c


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    monkeypatch.setenv("AMLCHECK_HOME", str(h))
    for var in ("AMLCHECK_CONFIG", "AMLCHECK_HYPERSYNC_TOKEN", "AMLCHECK_TRONGRID_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    return h


def check(web: TestClient, address: str) -> str:
    r = web.post("/check", data={"address": address, "token": TOKEN})
    assert r.status_code == 303
    return str(r.headers["location"]).rsplit("/", 1)[1]


def test_case_from_the_web(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    w = World(monkeypatch)
    w.rules[TRON] = ("R-HEU-07", "R-EXP-01")
    web = client(home, "sobhan")
    review = check(web, TRON)
    clean = check(web, OTHER)
    assert "Open a case" in web.get(f"/checks/{review}").text
    assert "Open a case" not in web.get(f"/checks/{clean}").text  # NO_HITS
    assert web.post("/cases", data={"check_id": review}).status_code == 403  # no form token
    r = web.post("/cases", data={"check_id": review, "token": TOKEN})
    assert r.status_code == 303
    case_url = str(r.headers["location"])
    page = web.get(case_url).text
    assert "Record the decision as sobhan" in page
    assert "No decision yet." in page
    assert f'Open case: <a href="{case_url}"' in web.get(f"/checks/{review}").text
    empty = web.post(
        f"{case_url}/decide", data={"decision": "approved", "note": " ", "token": TOKEN}
    )
    assert empty.status_code == 303
    assert "needs a note" in web.get(str(empty.headers["location"])).text
    ok = web.post(
        f"{case_url}/decide", data={"decision": "approved", "note": "known client", "token": TOKEN}
    )
    assert ok.headers["location"] == case_url
    closed = web.get(case_url).text
    assert "This case is closed" in closed
    assert "known client" in closed
    assert "Decisions on this check" in web.get(f"/checks/{review}").text
    listing = web.get("/cases").text
    assert "approved by sobhan" in listing
    assert web.get("/cases/nope").status_code == 404


def test_no_operator_name(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    w = World(monkeypatch)
    w.rules[TRON] = ("R-HEU-06",)
    web = client(home, None)
    review = check(web, TRON)
    r = web.post("/cases", data={"check_id": review, "token": TOKEN})
    assert r.status_code == 400
    assert "set [operator] name" in r.text


def test_reject_a_type_from_the_web(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    w = World(monkeypatch)
    w.rules[TRON] = ("R-HEU-07", "R-EXP-01")
    web = client(home, "sobhan")
    review = check(web, TRON)
    conn = sqlite3.connect(home / "amlcheck.db")
    p = collector(address=TRON, distinct_senders=120)
    clf.save(conn, Chain.TRON, p, clf.classify(p, ClassifyContext(), S, NOW), S, NOW)
    conn.commit()
    conn.close()
    case_url = str(
        web.post("/cases", data={"check_id": review, "token": TOKEN}).headers["location"]
    )
    assert "<b>COLLECTOR</b>" in web.get(case_url).text
    r = web.post(
        f"{case_url}/feedback", data={"type": "COLLECTOR", "verdict": "rejected", "token": TOKEN}
    )
    assert r.headers["location"] == case_url
    page = web.get(case_url).text
    assert "<b>COLLECTOR</b>" not in page
    assert "rejected COLLECTOR (classifier v1)" in page
    bad = web.post(
        f"{case_url}/feedback", data={"type": "HUB", "verdict": "confirmed", "token": TOKEN}
    )
    assert "not classified HUB" in web.get(str(bad.headers["location"])).text
