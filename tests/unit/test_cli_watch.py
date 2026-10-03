import json
import sqlite3
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from typer.testing import CliRunner

from amlcheck.cli import app, runtime
from amlcheck.core.models import Address, Chain
from amlcheck.monitor import alerts
from amlcheck.net.http import Mode
from amlcheck.storage.lock import run_lock
from tests.unit.test_engine import Fake

runner = CliRunner()
A = "TNHrhtVnRMRaTJFRsqLUqSytCKsavXJJaa"
B = "0x" + "12" * 20
HOOK = "http://127.0.0.1:8099/amlcheck"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    monkeypatch.setenv("AMLCHECK_HOME", str(h))
    for var in ("AMLCHECK_CONFIG", "AMLCHECK_HYPERSYNC_TOKEN", "AMLCHECK_TRONGRID_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    return h


class World:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.rules: dict[str, tuple[str, ...]] = {}
        self.traced: list[bool] = []
        self.notified: list[str] = []
        world = self

        class Source(Fake):
            async def check(self, address: Address) -> Any:
                self.rules = world.rules.get(address.norm, ())
                return await super().check(address)

        def sources(
            rt: Any, conn: Any, client: Any, mode: Mode, chain: Chain, *, trace: bool = False
        ) -> list[Fake]:
            world.traced.append(trace)
            return [Source("ofac_sdn")]

        monkeypatch.setattr(runtime, "make_screening_sources", sources)

        def notify(message: str) -> bool:
            world.notified.append(message)
            return True

        monkeypatch.setattr(alerts, "notify", notify)


def db(home: Path) -> sqlite3.Connection:
    return sqlite3.connect(home / "amlcheck.db")


def test_add_list_remove(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    World(monkeypatch)
    assert runner.invoke(app, ["watch", "add", A, "--client", "acme"]).exit_code == 0
    again = runner.invoke(app, ["watch", "add", A, "--note", "big client"])
    assert "already watched; updated" in again.output
    assert runner.invoke(app, ["watch", "add", B]).exit_code == 0
    rows = json.loads(runner.invoke(app, ["watch", "list", "--json"]).stdout)
    assert [(r["address"], r["client"], r["note"]) for r in rows] == [
        (A, "acme", "big client"),
        (B, None, None),
    ]
    assert "not run" in runner.invoke(app, ["watch", "list"]).output
    assert runner.invoke(app, ["watch", "remove", B]).exit_code == 0
    gone = runner.invoke(app, ["watch", "remove", B])
    assert gone.exit_code == 1
    assert "is not watched" in gone.output
    assert runner.invoke(app, ["watch", "add", "nonsense"]).exit_code == 1


# AT-46: `watch run` where one verdict changed → change reported, recorded, exit 6.
def test_at46_changed_verdict(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    w = World(monkeypatch)
    runner.invoke(app, ["watch", "add", A, "--client", "acme"])
    runner.invoke(app, ["watch", "add", B])
    first = runner.invoke(app, ["watch", "run"])
    assert first.exit_code == 0, first.output  # the first run of an address is not a change
    assert first.output.count("new     NO_HITS") == 2
    assert w.notified == []
    w.rules[B] = ("R-HEU-06",)
    second = runner.invoke(app, ["watch", "run"])
    assert second.exit_code == 6, second.output
    assert f"CHANGED NO_HITS → REVIEW · 20 · medium  bsc {B}" in second.output
    assert f"same    NO_HITS · 0 · low  tron {A}" in second.output
    assert "2 watched, 1 changed" in second.output
    assert w.notified == [f"1 watched verdict(s) changed: {B[:8]}…{B[-6:]}: NO_HITS → REVIEW"]
    assert w.traced == [False] * 4  # no trace in a watch run (D-057)
    conn = db(home)
    row = conn.execute(
        "SELECT last_verdict, last_check_id FROM watchlist WHERE address_norm = ?", (B,)
    ).fetchone()
    last = conn.execute(
        "SELECT check_id, verdict, operator_note FROM checks ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    assert row == ("REVIEW", last[0])  # B was checked last; its row points at that check
    assert last[1:] == ("REVIEW", "watch run")
    assert conn.execute("SELECT count(*) FROM checks").fetchone()[0] == 4
    third = runner.invoke(app, ["watch", "run", "--json"])
    assert third.exit_code == 0  # REVIEW again: no change
    data = json.loads(third.stdout)
    assert [r["before"] for r in data["results"]] == ["NO_HITS", "REVIEW"]
    assert data["changes"] == []


@respx.mock
def test_webhook(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    w = World(monkeypatch)
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(f'[monitor]\nwebhook_url = "{HOOK}"\n')
    hook = respx.post(HOOK).mock(return_value=httpx.Response(204))
    runner.invoke(app, ["watch", "add", B, "--client", "acme"])
    runner.invoke(app, ["watch", "run"])
    assert not hook.called  # nothing changed yet
    w.rules[B] = ("R-SAN-01",)
    r = runner.invoke(app, ["watch", "run"])
    assert r.exit_code == 6
    sent = json.loads(hook.calls.last.request.content)
    assert sent["source"] == "amlcheck watch run"
    (change,) = sent["changes"]
    assert (change["address"], change["before"], change["after"], change["client"]) == (
        B,
        "NO_HITS",
        "BLOCK",
        "acme",
    )
    hook.mock(return_value=httpx.Response(500))
    w.rules[B] = ()
    failed = runner.invoke(app, ["watch", "run"])
    assert failed.exit_code == 6  # an alert never fails the run
    assert "warning: webhook answered HTTP 500" in failed.output


def test_one_run_at_a_time(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    World(monkeypatch)
    runner.invoke(app, ["watch", "add", A])
    with run_lock(home, "test"):
        r = runner.invoke(app, ["watch", "run"])
    assert r.exit_code == 1
    assert "another batch or watch run is in progress" in r.output


def test_empty_watchlist(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    World(monkeypatch)
    r = runner.invoke(app, ["watch", "run"])
    assert r.exit_code == 0
    assert "0 watched, 0 changed" in r.output


def test_webhook_url_must_be_http(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text('[monitor]\nwebhook_url = "file:///etc/passwd"\n')
    r = runner.invoke(app, ["watch", "list"])
    assert r.exit_code == 1
    assert "http:// or https://" in r.output


def test_notify_passes_the_message_as_an_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil
    import subprocess
    import sys

    calls: list[list[str]] = []

    def fake_run(args: list[str], **kw: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/osascript")
    monkeypatch.setattr(subprocess, "run", fake_run)
    evil = 'x" & do shell script "rm -rf ~" & "'
    assert alerts.notify(evil)
    (args,) = calls
    assert args[-2:] == [evil, "amlcheck"]  # data, not script
    assert all(evil not in a for a in args[:-2])
    monkeypatch.setattr(sys, "platform", "linux")
    assert alerts.notify("x") is False
