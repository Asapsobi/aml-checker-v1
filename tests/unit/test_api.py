import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from amlcheck.api.app import create_app
from amlcheck.cli import app as cli
from amlcheck.cli import runtime
from amlcheck.core.models import Chain, SourceStatus
from amlcheck.net.http import Mode
from tests.unit.test_engine import Fake
from tests.unit.test_web import TRON, World
from tests.unit.trace_world import T

TOKEN = "k" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}
BASE = "http://127.0.0.1:8766"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    monkeypatch.setenv("AMLCHECK_HOME", str(h))
    for var in (
        "AMLCHECK_CONFIG",
        "AMLCHECK_HYPERSYNC_TOKEN",
        "AMLCHECK_TRONGRID_API_KEY",
        "AMLCHECK_API_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    return h


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> World:
    return World(monkeypatch)


@pytest.fixture
def api(home: Path, world: World) -> TestClient:
    rt = runtime.load()
    runtime.open_database(rt).close()
    return TestClient(create_app(rt, TOKEN), base_url=BASE, headers=AUTH)


def checks(home: Path) -> int:
    return int(
        sqlite3.connect(home / "amlcheck.db").execute("SELECT count(*) FROM checks").fetchone()[0]
    )


def is_problem(r: Any, status: int) -> dict[str, Any]:
    assert r.status_code == status, r.text
    assert r.headers["content-type"].startswith("application/problem+json")
    body: dict[str, Any] = r.json()
    assert body["status"] == status
    return body


# AT-57: a request without the token → 401; a short token → the server refuses to start.
def test_at57_token(api: TestClient, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bare = TestClient(api.app, base_url=BASE)
    r = bare.post("/v1/check", json={"address": TRON})
    body = is_problem(r, 401)
    assert body["title"] == "Missing or wrong token"
    assert r.headers["www-authenticate"].startswith("Bearer")
    wrong = bare.get("/v1/checks/x", headers={"Authorization": "Bearer " + "x" * 40})
    is_problem(wrong, 401)
    with pytest.raises(ValueError, match="at least 32"):
        create_app(runtime.load(), "short")
    started: list[object] = []
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: started.append(a))
    refused = CliRunner().invoke(cli, ["api"])
    assert refused.exit_code == 1
    assert "set AMLCHECK_API_TOKEN" in refused.output
    monkeypatch.setenv("AMLCHECK_API_TOKEN", "s" * 31)
    assert CliRunner().invoke(cli, ["api"]).exit_code == 1
    assert started == []
    monkeypatch.setenv("AMLCHECK_API_TOKEN", "s" * 32)
    ok = CliRunner().invoke(cli, ["api", "--port", "8799"])
    assert ok.exit_code == 0, ok.output
    assert len(started) == 1


def test_host_allow_list(api: TestClient) -> None:
    body = is_problem(api.get("/v1/checks/x", headers={"host": "evil.com"}), 400)
    assert body["title"] == "Host not allowed"
    assert api.get("/v1/checks/x", headers={"host": "localhost:8766"}).status_code == 404


# AT-55: POST /v1/check with an Idempotency-Key, repeated → the same check_id, no second record;
# the same key with another amount → 422.
def test_at55_idempotent_check(api: TestClient, home: Path, world: World) -> None:
    world.rules[TRON] = ("R-HEU-06",)
    key = {"Idempotency-Key": "order-1001"}
    first = api.post(
        "/v1/check", json={"address": TRON, "amount": "250", "client": "acme"}, headers=key
    )
    assert first.status_code == 200, first.text
    data = first.json()
    assert (data["verdict"], data["amount_usdt"], data["client"]) == ("REVIEW", "250", "acme")
    assert data["score"]["shown"] == "20 · low"
    again = api.post(
        "/v1/check", json={"address": TRON, "amount": "250.0", "client": "acme"}, headers=key
    )
    assert again.status_code == 200
    assert again.headers["idempotent-replayed"] == "true"
    assert again.content == first.content  # byte for byte
    assert checks(home) == 1
    other = api.post(
        "/v1/check", json={"address": TRON, "amount": "300", "client": "acme"}, headers=key
    )
    body = is_problem(other, 422)
    assert body["type"].endswith("/idempotency-key-mismatch")
    assert checks(home) == 1
    got = api.get(f"/v1/checks/{data['check_id']}")
    assert got.content == first.content  # the same JSON from the stored record


def test_in_progress_is_409(api: TestClient, home: Path) -> None:
    rt = runtime.load()
    conn = runtime.open_database(rt)
    from amlcheck.api import idempotency as idem

    idem.begin(
        conn,
        "busy-1",
        "check",
        idem.fingerprint(
            "check",
            {
                "address": TRON,
                "chain": "tron",
                "amount": None,
                "client": None,
                "note": None,
                "trace": False,
            },
        ),
        rt.clock(),
    )
    conn.close()
    body = is_problem(
        api.post("/v1/check", json={"address": TRON}, headers={"Idempotency-Key": "busy-1"}), 409
    )
    assert body["type"].endswith("/idempotency-in-progress")
    assert checks(home) == 0


def test_every_verdict_is_200(api: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def failing(
        rt: Any, conn: Any, client: Any, mode: Mode, chain: Chain, *, trace: bool = False
    ) -> list[Fake]:
        return [Fake("ofac_sdn", status=SourceStatus.ERROR)]

    monkeypatch.setattr(runtime, "make_screening_sources", failing)
    r = api.post("/v1/check", json={"address": TRON})
    assert r.status_code == 200
    assert r.json()["verdict"] == "INCOMPLETE"
    assert r.json()["score"]["shown"].startswith("≥ ")


def test_bad_requests(api: TestClient) -> None:
    body = is_problem(api.post("/v1/check", json={"address": "nonsense"}), 422)
    assert body["type"].endswith("/invalid-address")
    body = is_problem(api.post("/v1/check", json={"address": TRON, "colour": "red"}), 422)
    assert body["type"].endswith("/invalid-request")
    assert body["errors"][0]["field"] == "colour"
    is_problem(api.post("/v1/check", json={"address": TRON, "amount": "-1"}), 422)
    is_problem(
        api.post("/v1/check", json={"address": TRON}, headers={"Idempotency-Key": "x" * 300}), 400
    )
    is_problem(api.get("/v1/checks/nope"), 404)
    is_problem(api.get("/v1/nothing"), 404)
    is_problem(api.get("/v1/counterparties/eth/0x00"), 404)


# AT-56: POST /v1/traces, poll, repeat POST → 202 + id → done; the repeat returns the same id.
def test_at56_trace(api: TestClient, home: Path) -> None:
    key = {"Idempotency-Key": "trace-T-1"}
    r = api.post("/v1/traces", json={"address": T}, headers=key)
    assert r.status_code == 202, r.text
    trace_id = r.json()["trace_id"]
    assert r.headers["location"] == f"/v1/traces/{trace_id}"
    polled = api.get(f"/v1/traces/{trace_id}")
    assert polled.status_code == 200
    job = polled.json()
    assert (job["status"], job["requested_by"], job["complete"]) == ("done", "api", True)
    assert job["result"]["partition"]["exchange_regulated"] == "0.6"
    again = api.post("/v1/traces", json={"address": T}, headers=key)
    assert again.status_code == 202
    assert again.json()["trace_id"] == trace_id
    assert again.headers["idempotent-replayed"] == "true"
    assert (
        sqlite3.connect(home / "amlcheck.db").execute("SELECT count(*) FROM traces").fetchone()[0]
        == 1
    )
    is_problem(api.post("/v1/traces", json={"address": T, "direction": "out"}, headers=key), 422)
    is_problem(api.get("/v1/traces/nope"), 404)


def test_counterparty(api: TestClient, home: Path, world: World) -> None:
    world.rules[TRON] = ("R-HEU-06",)
    api.post("/v1/check", json={"address": TRON, "client": "acme"})
    r = api.get(f"/v1/counterparties/tron/{TRON}")
    assert r.status_code == 200
    data = r.json()
    assert data["checked"] is True
    assert data["registry"]["last_verdict"] == "REVIEW"
    assert data["registry"]["last_score_shown"] == "20 · low"
    assert data["registry"]["clients"] == ["acme"]
    assert (data["own_wallet"], data["watched"], data["open_case"], data["latest_decision"]) == (
        False,
        False,
        None,
        None,
    )
    never = api.get("/v1/counterparties/bsc/" + "0x" + "12" * 20).json()
    assert never["checked"] is False
    assert never["registry"] is None


def test_decision_shows_on_the_counterparty(api: TestClient, home: Path, world: World) -> None:
    world.rules[TRON] = ("R-HEU-06",)
    check_id = api.post("/v1/check", json={"address": TRON}).json()["check_id"]
    rt = runtime.load()
    conn = runtime.open_database(rt)
    from amlcheck.cases import cases
    from amlcheck.core.address import detect

    case, _ = cases.open_case(conn, detect(TRON), by="sobhan", now=rt.clock())
    cases.decide(conn, case, "approved", "fine", by="sobhan", now=rt.clock(), tool_version="x")
    conn.close()
    data = api.get(f"/v1/counterparties/tron/{TRON}").json()
    assert data["latest_decision"]["decision"] == "approved"
    assert data["latest_decision"]["check_id"] == check_id
    assert api.get(f"/v1/checks/{check_id}").json()["decisions"][0]["decision"] == "approved"
