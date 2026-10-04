import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from amlcheck.chain.base import Transfer
from amlcheck.cli import app, runtime
from amlcheck.core.models import Address, Chain
from amlcheck.monitor import alerts
from amlcheck.net.http import Mode
from amlcheck.storage.lock import run_lock
from tests.unit.test_engine import Fake as Source
from tests.unit.trace_world import Fake, addr

runner = CliRunner()
W = addr("W")
S1, S2 = addr("s1"), addr("s2")


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
        now = datetime.now(UTC)
        self.transfers = [
            Transfer(
                Chain.BSC, "0x" + "a" * 64, 0, None, now - timedelta(hours=2), S1, W, Decimal(50)
            ),
            Transfer(
                Chain.BSC,
                "0x" + "b" * 64,
                0,
                None,
                now - timedelta(hours=1),
                S2,
                W,
                Decimal(12_000),
            ),
        ]
        self.rules: dict[str, tuple[str, ...]] = {S2: ("R-HEU-02",)}
        self.screened: list[str] = []
        self.traced: list[bool] = []
        self.notified: list[str] = []
        world = self
        chain = Fake(self.transfers)

        class Recorder(Source):
            async def check(self, address: Address) -> Any:
                world.screened.append(address.norm)
                self.rules = world.rules.get(address.norm, ())
                return await super().check(address)

        def sources(
            rt: Any, conn: Any, client: Any, mode: Mode, chain_: Chain, *, trace: bool = False
        ) -> list[Source]:
            world.traced.append(trace)
            return [Recorder("exposure")]

        def history_sources(rt: Any, client: Any, mode: Mode, **kw: Any) -> dict[Chain, Any]:
            assert mode is Mode.BACKGROUND  # a scheduled job may wait out a suspension (D-036)
            return {Chain.BSC: chain, Chain.TRON: chain}

        def notify(message: str) -> bool:
            world.notified.append(message)
            return True

        monkeypatch.setattr(runtime, "make_screening_sources", sources)
        monkeypatch.setattr(runtime, "make_sources", history_sources)
        monkeypatch.setattr(alerts, "notify", notify)


def test_wallets_commands(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    World(monkeypatch)
    assert runner.invoke(app, ["wallets", "add", W]).exit_code != 0  # --name is required
    r = runner.invoke(app, ["wallets", "add", W, "--name", "hot wallet"])
    assert r.exit_code == 0, r.output
    assert "added: bsc" in r.output
    assert "not monitored yet" in runner.invoke(app, ["wallets", "list"]).output
    shown = runner.invoke(app, ["intel", "show", W]).output
    assert "own_or_trusted" in shown
    rows = json.loads(runner.invoke(app, ["wallets", "list", "--json"]).stdout)
    assert [(x["address"], x["name"], x["active"]) for x in rows] == [(W, "hot wallet", True)]
    assert runner.invoke(app, ["wallets", "remove", W]).exit_code == 0
    assert runner.invoke(app, ["wallets", "remove", W]).exit_code == 1
    assert "0 own wallet(s)" in runner.invoke(app, ["wallets", "list"]).output


# AT-52 through the CLI: a never-screened sender is screened; REVIEW → exit 6, alerted.
def test_at52_monitor_run_exit_6(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    w = World(monkeypatch)
    assert "no own wallets" in runner.invoke(app, ["monitor", "run"]).output
    runner.invoke(app, ["wallets", "add", W, "--name", "hot wallet"])
    r = runner.invoke(app, ["monitor", "run"])
    assert r.exit_code == 6, r.output
    assert w.screened == [S1, S2]
    assert w.traced == [False, True]  # 12,000 USDT ≥ [monitor] trace_amount_usdt
    assert f"ATTENTION REVIEW · 10 · low  {S2}" in r.output
    assert "2 sender(s) screened, 1 need attention" in r.output
    assert len(w.notified) == 1
    again = runner.invoke(app, ["monitor", "run", "--json"])
    assert again.exit_code == 0  # nothing new since the last run
    data = json.loads(again.stdout)
    assert data["attention"] == []
    assert data["wallets"][0]["new_senders"] == 0
    assert w.screened == [S1, S2]
    assert "monitored up to" in runner.invoke(app, ["wallets", "list"]).output


# AT-54: two `monitor run`s at once → the second exits "already running" and screens nothing.
def test_at54_one_run_at_a_time(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    w = World(monkeypatch)
    runner.invoke(app, ["wallets", "add", W, "--name", "hot wallet"])
    with run_lock(home, "another monitor run"):
        r = runner.invoke(app, ["monitor", "run"])
    assert r.exit_code == 1
    assert "already running" in r.output
    assert w.screened == []
