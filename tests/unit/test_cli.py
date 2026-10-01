import json
from importlib.metadata import version
from pathlib import Path

import pytest
from typer.testing import CliRunner

from amlcheck.cli import app
from amlcheck.storage.db import connect, load_migrations, schema_version

runner = CliRunner()


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    monkeypatch.setenv("AMLCHECK_HOME", str(h))
    monkeypatch.delenv("AMLCHECK_CONFIG", raising=False)
    return h


def test_version(home: Path) -> None:
    r = runner.invoke(app, ["--version"])
    assert r.exit_code == 0
    assert r.output.strip() == f"amlcheck {version('amlcheck')}"


# AT-01: empty AMLCHECK_HOME → --version and status run; DB created at the latest schema.
def test_status_on_empty_home(home: Path) -> None:
    assert not home.exists()
    r = runner.invoke(app, ["status"])
    assert r.exit_code == 0, r.output
    assert str(home) in r.output
    assert "using defaults" in r.output
    db = home / "amlcheck.db"
    assert db.exists()
    assert schema_version(connect(db)) == len(load_migrations())


def test_status_json(home: Path) -> None:
    r = runner.invoke(app, ["status", "--json"])
    assert r.exit_code == 0, r.output
    info = json.loads(r.output)
    assert info["home"] == str(home)
    assert info["config_found"] is False
    assert info["db_schema"] == info["db_schema_latest"]
    assert len(info["config_hash"]) == 64


# AT-02 through the CLI: refused at load with a clear message, exit 1.
def test_status_refuses_bad_config(home: Path) -> None:
    home.mkdir()
    (home / "config.toml").write_text("[exposure]\nlookbak_days = 90\n")
    r = runner.invoke(app, ["status"])
    assert r.exit_code == 1
    assert "[exposure] lookbak_days: unknown key" in r.output
    assert not (home / "amlcheck.db").exists()


def test_config_env_override(home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / "custom.toml"
    cfg.write_text("[trace]\nbranch = 6\n")
    monkeypatch.setenv("AMLCHECK_CONFIG", str(cfg))
    r = runner.invoke(app, ["status", "--json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["config"] == str(cfg)
    assert json.loads(r.output)["config_found"] is True


def test_no_args_shows_help(home: Path) -> None:
    r = runner.invoke(app, [])
    assert "status" in r.output
