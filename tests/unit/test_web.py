import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from amlcheck.cli import runtime
from amlcheck.core.models import Address, Chain
from amlcheck.net.http import Mode
from amlcheck.trace.engine import TraceEngine
from amlcheck.trace.jobs import TraceJobs
from amlcheck.web.app import COOKIE, create_app
from tests.unit.test_engine import Fake
from tests.unit.trace_world import NOW, T, engine, example, setup_example
from tests.unit.trace_world import Fake as ChainFake

TOKEN = "t0k3n-for-tests"
TRON = "TNHrhtVnRMRaTJFRsqLUqSytCKsavXJJaa"
BASE = "http://127.0.0.1:8765"


class World:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.rules: dict[str, tuple[str, ...]] = {}
        self.traced: list[bool] = []
        world = self

        class Source(Fake):
            async def check(self, address: Address) -> Any:
                self.rules = world.rules.get(address.norm, ())
                return await super().check(address)

        def sources(
            rt: Any, conn: Any, client: Any, mode: Mode, chain: Chain, *, trace: bool = False
        ) -> list[Fake]:
            world.traced.append(trace)
            return [Source("ofac_sdn"), Source("exposure")]

        def build(rt: Any, conn: sqlite3.Connection, client: Any, mode: Mode) -> TraceEngine:
            assert mode is Mode.BACKGROUND
            eng, store = engine(conn, ChainFake(example()))
            setup_example(conn, store)
            return eng

        monkeypatch.setattr(runtime, "make_screening_sources", sources)
        monkeypatch.setattr(runtime, "build_trace_engine", build)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    monkeypatch.setenv("AMLCHECK_HOME", str(h))
    for var in ("AMLCHECK_CONFIG", "AMLCHECK_HYPERSYNC_TOKEN", "AMLCHECK_TRONGRID_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    return h


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> World:
    return World(monkeypatch)


@pytest.fixture
def anon(home: Path) -> TestClient:
    rt = runtime.load()
    runtime.open_database(rt).close()
    return TestClient(create_app(rt, TOKEN), base_url=BASE, follow_redirects=False)


@pytest.fixture
def web(anon: TestClient) -> TestClient:
    r = anon.get(f"/login?t={TOKEN}")
    assert r.status_code == 303
    return anon


def db(home: Path) -> sqlite3.Connection:
    return sqlite3.connect(home / "amlcheck.db")


# AT-48: a request with `Host: evil.com` → 400; a POST without the start-up token → 403.
def test_at48_host_allow_list(anon: TestClient) -> None:
    r = anon.get("/login", params={"t": TOKEN}, headers={"host": "evil.com"})
    assert r.status_code == 400
    assert anon.get(f"/login?t={TOKEN}", headers={"host": "localhost:8765"}).status_code == 303


def test_at48_token(anon: TestClient, world: World, home: Path) -> None:
    assert anon.get("/").status_code == 403  # no cookie yet
    assert anon.get("/login?t=wrong").status_code == 403
    r = anon.get(f"/login?t={TOKEN}")
    assert r.status_code == 303
    cookie = r.headers["set-cookie"]
    assert f"{COOKIE}={TOKEN}" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie or "samesite=strict" in cookie.lower()
    assert anon.get("/").status_code == 200
    refused = anon.post("/check", data={"address": TRON})  # cookie, but no form token
    assert refused.status_code == 403
    wrong = anon.post("/check", data={"address": TRON, "token": "nope"})
    assert wrong.status_code == 403
    assert not (home / "amlcheck.db").exists() or (
        db(home).execute("SELECT count(*) FROM checks").fetchone()[0] == 0
    )
    ok = anon.post("/check", data={"address": TRON, "token": TOKEN})
    assert ok.status_code == 303


def test_security_headers_and_no_scripts(web: TestClient) -> None:
    r = web.get("/")
    csp = r.headers["content-security-policy"]
    assert "default-src 'none'" in csp
    assert "script-src" not in csp  # falls back to 'none'
    assert "frame-ancestors 'none'" in csp
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert "<script" not in r.text.lower()
    css = web.get("/static/app.css")
    assert css.status_code == 200
    assert css.headers["content-type"].startswith("text/css")


def test_check_flow(web: TestClient, world: World, home: Path) -> None:
    world.rules[TRON] = ("R-HEU-06",)
    r = web.post(
        "/check", data={"address": TRON, "client": "acme", "note": "first", "token": TOKEN}
    )
    assert r.status_code == 303
    detail = web.get(r.headers["location"])
    assert detail.status_code == 200
    page = detail.text
    assert "REVIEW" in page
    assert "Score <b>20 · low</b>" in page
    assert "R-HEU-06" in page
    assert f"https://tronscan.org/#/address/{TRON}" in page
    assert 'rel="noopener noreferrer"' in page
    assert "matches its contents" in page
    check_id = r.headers["location"].rsplit("/", 1)[1]
    pdf = web.get(f"/checks/{check_id}/report.pdf")
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF-")
    assert world.traced == [False]
    row = db(home).execute("SELECT client, operator_note, verdict FROM checks").fetchone()
    assert row == ("acme", "first", "REVIEW")


def test_bad_input_records_nothing(web: TestClient, world: World, home: Path) -> None:
    r = web.post("/check", data={"address": "nonsense", "token": TOKEN})
    assert r.status_code == 400
    assert "error" in r.text
    r2 = web.post("/check", data={"address": TRON, "amount": "-3", "token": TOKEN})
    assert r2.status_code == 400
    assert "positive number" in r2.text
    assert db(home).execute("SELECT count(*) FROM checks").fetchone()[0] == 0


def test_amount_turns_the_trace_on(web: TestClient, world: World) -> None:
    web.post("/check", data={"address": TRON, "amount": "10000", "token": TOKEN})
    web.post("/check", data={"address": TRON, "amount": "50", "trace": "1", "token": TOKEN})
    web.post("/check", data={"address": TRON, "amount": "50", "token": TOKEN})
    assert world.traced == [True, True, False]


def test_history_and_counterparties(web: TestClient, world: World) -> None:
    world.rules[TRON] = ("R-HEU-06",)
    web.post("/check", data={"address": TRON, "client": "<script>x</script>", "token": TOKEN})
    world.rules[TRON] = ()
    web.post("/check", data={"address": T, "token": TOKEN})
    h = web.get("/checks")
    assert h.status_code == 200
    assert TRON[:8] in h.text
    assert T[:8] in h.text
    assert "<script>x</script>" not in h.text  # escaped
    assert "&lt;script&gt;x&lt;/script&gt;" in h.text
    only = web.get("/checks", params={"verdict": "REVIEW"}).text
    assert TRON[:8] in only
    assert T[:8] not in only
    bad = web.get("/checks", params={"address": "nope"})
    assert bad.status_code == 200
    assert "error" in bad.text
    cps = web.get("/counterparties")
    assert f"/counterparties/tron/{TRON}" in cps.text
    one = web.get(f"/counterparties/tron/{TRON}")
    assert one.status_code == 200
    assert "Last verdict" in one.text
    assert 'name="token"' in one.text  # the trace form carries the token
    assert web.get("/counterparties/tron/nope").status_code == 404
    assert web.get("/checks/no-such-check").status_code == 404


def test_trace_from_the_web(web: TestClient, world: World, home: Path) -> None:
    assert web.post("/trace", data={"address": T}).status_code == 403  # no form token
    r = web.post("/trace", data={"address": T, "token": TOKEN})
    assert r.status_code == 303
    page = web.get(r.headers["location"])  # the background task has run
    assert page.status_code == 200
    assert 'http-equiv="refresh"' not in page.text
    assert "exchange_regulated" in page.text
    assert "<svg" in page.text
    assert "coverage 90.0%" in page.text
    status = db(home).execute("SELECT status, requested_by FROM traces").fetchone()
    assert status == ("done", "web")


def test_running_trace_page_refreshes(web: TestClient, home: Path) -> None:
    rt = runtime.load()
    conn = runtime.open_database(rt)
    trace_id = TraceJobs(conn, rt.settings, clock=lambda: NOW).create(Chain.BSC, T)
    conn.close()
    page = web.get(f"/traces/{trace_id}")
    assert page.status_code == 200
    assert '<meta http-equiv="refresh" content="2">' in page.text
    assert "Queued" in page.text
    assert web.get("/traces/nope").status_code == 404


# P12: the check page says who the address is, the risk lines and the exposures behind the score.
async def test_check_page_shows_exposures(web: TestClient, home: Path) -> None:
    from amlcheck.core.address import detect
    from amlcheck.core.clock import fixed, to_db
    from amlcheck.core.engine import screen
    from tests.unit.test_exposure import ME, source, tr
    from tests.unit.test_exposure import NOW as E_NOW

    rt = runtime.load()
    conn = runtime.open_database(rt)
    conn.execute(
        "INSERT INTO list_snapshots (id, source, fetched_at, published_at, sha256, entry_count, "
        "address_count) VALUES (1, 'ofac_sdn', ?, '2026-09-30', 'h', 1, 1)",
        (to_db(E_NOW),),
    )
    conn.execute(
        "INSERT INTO sanctioned_addresses VALUES (1, 'TSANCTIONED', 'USDT', '42', 'Bad Co', 'X', 1)"
    )
    result = await screen(
        detect(ME),
        [source(conn, [tr(5, "TSANCTIONED", "600"), tr(4, "TOK", "9400")])],
        conn=conn,
        settings=rt.settings,
        now=fixed(E_NOW),
    )
    conn.close()
    page = web.get(f"/checks/{result.check_id}").text
    assert "Sanctioned entity: direct received 6.0%" in page
    assert "<h2>Exposures</h2>" in page
    assert "OFAC SDN: Bad Co" in page
    assert "sanctioned_entity" in page
