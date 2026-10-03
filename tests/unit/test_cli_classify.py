import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from amlcheck.cli import app, runtime
from amlcheck.core.models import Chain
from tests.unit.test_cache import Clock, FakeSource
from tests.unit.test_profiler import DEP, HUB, NOW, NoContracts, world

runner = CliRunner()


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    monkeypatch.setenv("AMLCHECK_HOME", str(h))
    for var in ("AMLCHECK_CONFIG", "AMLCHECK_HYPERSYNC_TOKEN", "AMLCHECK_TRONGRID_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    src = FakeSource(Clock(NOW), world(), chain=Chain.BSC)
    monkeypatch.setattr(runtime, "make_sources", lambda rt, c, m: {Chain.BSC: src})
    monkeypatch.setattr(
        runtime, "make_contract_lookups", lambda rt, http: {Chain.BSC: NoContracts()}
    )
    real_load = runtime.load

    def load() -> Any:
        rt = real_load()
        return runtime.Runtime(rt.paths, rt.settings, rt.secrets, clock=lambda: NOW)

    monkeypatch.setattr(runtime, "load", load)
    return h


def test_classify_and_entities(home: Path) -> None:
    r = runner.invoke(app, ["classify", DEP, "--json"])
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    (primary,) = [t for t in out["types"] if t["primary"]]
    assert (primary["type"], primary["confidence"]) == ("DEPOSIT", "0.9")
    assert out["entity"]["role"] == "deposit"
    assert out["profile"]["distinct_senders"] == 8
    human = runner.invoke(app, ["classify", DEP])
    assert "DEPOSIT       0.9   primary" in human.output
    assert "never a verdict" in human.output

    listed = json.loads(runner.invoke(app, ["intel", "entity", "list", "--json"]).output)
    (entity,) = listed
    assert (entity["name"], entity["kind"], entity["members"]) == (f"hub-{HUB[:8]}", "unknown", 2)
    shown = runner.invoke(app, ["intel", "entity", "show", str(entity["id"])])
    assert f"hub      {HUB}" in shown.output
    r = runner.invoke(
        app,
        ["intel", "entity", "name", str(entity["id"]), "Binance", "--kind", "exchange_regulated"],
    )
    assert r.exit_code == 0, r.output
    again = json.loads(runner.invoke(app, ["classify", DEP, "--json"]).output)
    assert again["decisive_category"] == "exchange_regulated"
    (p2,) = [t for t in again["types"] if t["primary"]]
    assert p2["confidence"] == "1.0"  # + the hub's entity is named by the operator


def test_entity_errors(home: Path) -> None:
    assert runner.invoke(app, ["intel", "entity", "show", "9"]).exit_code == 1
    r = runner.invoke(app, ["intel", "entity", "name", "9", "X", "--kind", "exchange_regulated"])
    assert r.exit_code == 1
    assert "no entity #9" in r.output


def test_label_without_by_records_operator(home: Path) -> None:
    runner.invoke(app, ["intel", "label", HUB, "exchange_regulated"])
    shown = json.loads(runner.invoke(app, ["intel", "show", HUB, "--json"]).output)
    assert shown["labels"][0]["created_by"] == "operator"
