"""The v1 JSON contract (D-068): fields may be added, never removed, renamed or given a new meaning.

If one of these tests fails because a field was removed or renamed, that is a breaking change: it
belongs in v2. Adding a field means adding it here too.
"""

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from amlcheck.api.app import create_app
from amlcheck.cli import runtime
from amlcheck.config import Settings
from amlcheck.core.address import detect
from amlcheck.core.clock import fixed
from amlcheck.core.engine import screen
from amlcheck.report.check_json import check_json
from amlcheck.storage.db import open_db
from tests.unit.test_engine import Fake
from tests.unit.trace_world import NOW, T

CHECK = {
    "check_id",
    "verdict",
    "action",
    "chain",
    "address",
    "checked_at",
    "amount_usdt",
    "client",
    "note",
    "score",
    "findings",
    "sources",
    "trace_id",
    "decisions",
    "audit",
    "tool_version",
    "config_hash",
    "disclaimer",
}
SCORE = {"score_version", "score", "band", "lower_bound", "components", "hazard", "shown"}
COMPONENTS = {"E", "D", "B", "U"}
FINDING = {"rule_id", "severity", "source", "summary", "observed_at", "evidence"}
SOURCE = {"source", "label", "required", "status", "detail", "observed_at", "evidence"}
ROUTES = {
    ("POST", "/v1/check"),
    ("GET", "/v1/checks/{check_id}"),
    ("POST", "/v1/traces"),
    ("GET", "/v1/traces/{trace_id}"),
    ("GET", "/v1/counterparties/{chain}/{address}"),
}
TRACE = {
    "trace_id",
    "status",
    "chain",
    "address",
    "direction",
    "requested_by",
    "created_at",
    "started_at",
    "finished_at",
    "progress",
    "failure_reason",
    "result",
    "partial",
    "complete",
}
COUNTERPARTY = {
    "chain",
    "address",
    "checked",
    "registry",
    "category",
    "labels",
    "entity",
    "own_wallet",
    "watched",
    "open_case",
    "latest_decision",
}
VERDICTS = {"BLOCK", "INCOMPLETE", "REVIEW", "NO_HITS"}
BANDS = {"low", "medium", "high", "severe"}


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / "a.db")


async def test_check_json_fields(conn: sqlite3.Connection) -> None:
    r = await screen(
        detect(T),
        [Fake("ofac_sdn"), Fake("exposure", rules=("R-HEU-02",))],
        conn=conn,
        settings=Settings(),
        now=fixed(NOW),
    )
    data: dict[str, Any] | None = check_json(conn, r.check_id)
    assert data is not None
    assert set(data) >= CHECK
    assert data["verdict"] in VERDICTS
    assert set(data["score"]) >= SCORE
    assert set(data["score"]["components"]) >= COMPONENTS
    assert data["score"]["band"] in BANDS
    assert set(data["findings"][0]) >= FINDING
    assert set(data["sources"][0]) >= SOURCE
    assert set(data["audit"]) >= {"record_hash"}


def test_api_routes_and_shapes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AMLCHECK_HOME", str(tmp_path / "home"))
    for var in ("AMLCHECK_CONFIG", "AMLCHECK_HYPERSYNC_TOKEN", "AMLCHECK_TRONGRID_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    rt = runtime.load()
    runtime.open_database(rt).close()
    app = create_app(rt, "c" * 40)
    routes = {
        (method, route.path)  # type: ignore[attr-defined]
        for route in app.routes
        for method in getattr(route, "methods", set())
        if route.path.startswith("/v1/")  # type: ignore[attr-defined]
    }
    assert routes >= ROUTES
    client = TestClient(
        app, base_url="http://127.0.0.1", headers={"Authorization": "Bearer " + "c" * 40}
    )
    cp = client.get(f"/v1/counterparties/bsc/{T}").json()
    assert set(cp) >= COUNTERPARTY
    problem = client.get("/v1/traces/none")
    assert problem.headers["content-type"].startswith("application/problem+json")
    assert {"type", "title", "status"} <= set(problem.json())


def test_trace_json_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.unit.test_web import World

    World(monkeypatch)
    monkeypatch.setenv("AMLCHECK_HOME", str(tmp_path / "home"))
    for var in ("AMLCHECK_CONFIG", "AMLCHECK_HYPERSYNC_TOKEN", "AMLCHECK_TRONGRID_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    rt = runtime.load()
    runtime.open_database(rt).close()
    auth = {"Authorization": "Bearer " + "c" * 40}
    client = TestClient(create_app(rt, "c" * 40), base_url="http://127.0.0.1", headers=auth)
    trace_id = client.post("/v1/traces", json={"address": T}).json()["trace_id"]
    job = client.get(f"/v1/traces/{trace_id}").json()
    assert set(job) >= TRACE
    assert job["status"] == "done"
    assert {"partition", "coverage", "paths", "budget", "nodes", "edges"} <= set(job["result"])
