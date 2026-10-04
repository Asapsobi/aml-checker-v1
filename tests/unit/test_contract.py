"""The v2 JSON contract (D-073): fields may be added, never removed, renamed or given a new meaning.

If one of these tests fails because a field was removed or renamed, that is a breaking change: it
belongs in a v3. Adding a field means adding it here too. v1 (D-068) ended with amlcheck 2: its
paths answer 410, and checks it stored still read back.
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
    "contract",
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
    "address_label",
    "detail_list",
    "exposures",
    "findings",
    "sources",
    "trace_id",
    "decisions",
    "audit",
    "tool_version",
    "config_hash",
    "disclaimer",
}
SCORE = {"score_version", "score", "level", "lower_bound", "components", "hazard", "shown"}
SCORE |= {"decay", "k"}
COMPONENTS = {"X", "B"}
EXPOSURE = {
    "direction",
    "exposure_type",
    "hop",
    "address",
    "entity",
    "category",
    "risk_type",
    "volume_usdt",
    "percent",
    "inferred",
    "confidence",
    "path",
}
LABEL = {"name", "source", "category", "inferred", "confidence"}
FINDING = {"rule_id", "severity", "source", "summary", "observed_at", "evidence"}
SOURCE = {"source", "label", "required", "status", "detail", "observed_at", "evidence"}
ROUTES = {
    ("POST", "/v2/check"),
    ("GET", "/v2/checks/{check_id}"),
    ("POST", "/v2/traces"),
    ("GET", "/v2/traces/{trace_id}"),
    ("GET", "/v2/counterparties/{chain}/{address}"),
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
LEVELS = {"low", "moderate", "high", "severe"}


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / "a.db")


# AT-64 (fields): contract 2, with exposures, the detail lines and the address's label.
async def test_check_json_fields(conn: sqlite3.Connection) -> None:
    from tests.unit.test_label import Classified
    from tests.unit.test_score_check import Exposed

    r = await screen(
        detect(T),
        [Fake("ofac_sdn"), Exposed("0.05", "R-HEU-02"), Classified("PERSONAL", "0.5")],
        conn=conn,
        settings=Settings(),
        now=fixed(NOW),
    )
    data: dict[str, Any] | None = check_json(conn, r.check_id)
    assert data is not None
    assert set(data) >= CHECK
    assert data["contract"] == 2
    assert data["verdict"] in VERDICTS
    assert set(data["exposures"][0]) >= EXPOSURE
    assert set(data["address_label"]) >= LABEL
    assert data["detail_list"] == ["Sanctioned entity: direct received 5.0%"]
    assert set(data["score"]) >= SCORE
    assert set(data["score"]["components"]) >= COMPONENTS
    assert data["score"]["level"] in LEVELS
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
        if route.path.startswith("/v2/")  # type: ignore[attr-defined]
    }
    assert routes >= ROUTES
    client = TestClient(
        app, base_url="http://127.0.0.1", headers={"Authorization": "Bearer " + "c" * 40}
    )
    cp = client.get(f"/v2/counterparties/bsc/{T}").json()
    assert set(cp) >= COUNTERPARTY
    problem = client.get("/v2/traces/none")
    assert problem.headers["content-type"].startswith("application/problem+json")
    assert {"type", "title", "status"} <= set(problem.json())
    # AT-64: v1 paths are gone, and say where to go.
    gone = client.post("/v1/check", json={"address": T})
    assert gone.status_code == 410
    assert gone.headers["content-type"].startswith("application/problem+json")
    assert gone.json()["use"] == "/v2/check"


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
    trace_id = client.post("/v2/traces", json={"address": T}).json()["trace_id"]
    job = client.get(f"/v2/traces/{trace_id}").json()
    assert set(job) >= TRACE
    assert job["status"] == "done"
    assert {"partition", "coverage", "paths", "budget", "nodes", "edges"} <= set(job["result"])


# AT-64: a check stored by v1 still renders, with its v1 score, no exposures and no label.
def test_v1_record_still_renders(conn: sqlite3.Connection) -> None:
    from amlcheck.core.audit import AuditRecord, append, verify

    v1 = (
        '{"band":"high","components":{"B":"5","D":"0","E":"59.5","U":"1"},"hazard":"0.24",'
        '"lower_bound":false,"score":66,"score_version":1}'
    )
    record = AuditRecord(
        "v1-check",
        "2026-09-01T00:00:00.000000Z",
        "tron",
        T,
        "REVIEW",
        "1.0.0",
        1,
        "h",
        score_json=v1,
    )
    append(conn, record)
    data = check_json(conn, "v1-check")
    assert data is not None
    assert data["contract"] == 2
    assert data["score"]["score_version"] == 1
    assert (data["score"]["band"], data["score"]["shown"]) == ("high", "66 · high")
    assert (data["exposures"], data["detail_list"], data["address_label"]) == ([], [], None)
    assert verify(conn).ok
