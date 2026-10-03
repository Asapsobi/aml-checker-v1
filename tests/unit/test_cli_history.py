import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from amlcheck.chain.base import History, HistorySource
from amlcheck.cli import app, runtime
from amlcheck.core.models import Chain
from amlcheck.net.http import Mode, SourceError
from tests.unit.test_cache import ME, Clock, FakeSource, peer, tr

runner = CliRunner()


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    h = tmp_path / "home"
    monkeypatch.setenv("AMLCHECK_HOME", str(h))
    monkeypatch.delenv("AMLCHECK_CONFIG", raising=False)
    monkeypatch.delenv("AMLCHECK_HYPERSYNC_TOKEN", raising=False)
    monkeypatch.delenv("AMLCHECK_TRONGRID_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)  # no ./.env from the repo
    return h


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeSource:
    now = datetime.now(UTC)
    src = FakeSource(Clock(now))
    base = (now - datetime(2026, 1, 1, tzinfo=UTC)).total_seconds()
    src.transfers += [tr(base - 3600, 5), tr(base - 7200, 6, out=True), tr(base - 86_400 * 400, 7)]
    src.zero.append((ME, now - timedelta(hours=3)))

    def sources(rt: Any, client: httpx.AsyncClient, mode: Mode) -> dict[Chain, HistorySource]:
        assert mode is Mode.CHECK
        return {Chain.BSC: src}

    monkeypatch.setattr(runtime, "make_sources", sources)
    return src


def test_history_json(home: Path, fake: FakeSource) -> None:
    r = runner.invoke(app, ["history", ME.upper().replace("0X", "0x"), "--json"])
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert out["chain"] == "bsc"
    assert out["address"] == ME
    assert out["complete"] is True
    assert out["zero_value_dropped"] == 1
    assert [(t["direction"], t["counterparty"]) for t in out["transfers"]] == [
        ("in", peer(5)),
        ("out", peer(6)),
    ]  # the 400-day-old transfer is outside the default 180 days
    assert out["transfers"][0]["amount_usdt"] == "0.7142857142857142857142857143"
    r2 = runner.invoke(app, ["history", ME, "--json"])
    assert r2.exit_code == 0
    assert len(fake.asked) == 1  # second read served from the cache


def test_history_since_and_limit(home: Path, fake: FakeSource) -> None:
    r = runner.invoke(app, ["history", ME, "--since", "500", "--limit", "1", "--json"])
    assert r.exit_code == 0, r.output
    out = json.loads(r.output)
    assert len(out["transfers"]) == 1
    assert out["complete"] is False


def test_history_human_output(home: Path, fake: FakeSource) -> None:
    r = runner.invoke(app, ["history", ME, "--since", "500", "--limit", "2"], terminal_width=200)
    assert r.exit_code == 0, r.output
    assert "BSC" in r.output
    assert peer(5) in r.output
    assert "Incomplete: more than 2 transfers" in r.output
    assert "0-value transfers dropped: 1" in r.output


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["history", "nonsense"], "not a TRON"),
        (["history", ME, "--since", "2026-09-01", "--until", "2026-08-01"], "before --since"),
    ],
)
def test_history_bad_input_exit_1(
    home: Path, fake: FakeSource, args: list[str], message: str
) -> None:
    r = runner.invoke(app, args)
    assert r.exit_code == 1
    assert message in r.output


def test_history_bad_since_is_usage_error(home: Path, fake: FakeSource) -> None:
    r = runner.invoke(app, ["history", ME, "--since", "last tuesday"])
    assert r.exit_code == 2


def test_history_source_error_exit_1(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Broken:
        chain = Chain.TRON

        async def fetch(self, *a: Any, **k: Any) -> History:
            raise SourceError("trongrid", "rate limited; suspended for 30 s")

    monkeypatch.setattr(runtime, "make_sources", lambda rt, c, m: {Chain.TRON: Broken()})
    r = runner.invoke(app, ["history", "TNHrhtVnRMRaTJFRsqLUqSytCKsavXJJaa"])
    assert r.exit_code == 1
    assert "error: trongrid: rate limited; suspended for 30 s" in r.output


def test_bsc_without_token_says_which_key(home: Path) -> None:
    r = runner.invoke(app, ["history", ME])
    assert r.exit_code == 1
    assert "AMLCHECK_HYPERSYNC_TOKEN" in r.output


def test_parse_when() -> None:
    now = datetime(2026, 10, 1, 12, tzinfo=UTC)
    assert runtime.parse_when("30", now, name="x") == now - timedelta(days=30)
    assert runtime.parse_when("2026-09-01", now, name="x") == datetime(2026, 9, 1, tzinfo=UTC)
    assert runtime.parse_when("2026-09-01T10:00:00+02:00", now, name="x") == datetime(
        2026, 9, 1, 8, tzinfo=UTC
    )
    assert runtime.parse_when("2026-09-01T10:00:00Z", now, name="x") == datetime(
        2026, 9, 1, 10, tzinfo=UTC
    )


def test_cache_stats_and_prune(home: Path, fake: FakeSource) -> None:
    assert runner.invoke(app, ["history", ME]).exit_code == 0
    r = runner.invoke(app, ["cache", "stats", "--json"])
    assert r.exit_code == 0, r.output
    stats = json.loads(r.output)
    assert stats["chains"]["bsc"]["addresses"] == 1
    assert stats["chains"]["bsc"]["transfers"] == 2
    r = runner.invoke(app, ["cache", "prune", "--days", "30"])
    assert r.exit_code == 0
    assert "forgot 0 address(es)" in r.output  # just used
    r = runner.invoke(app, ["cache", "stats"])
    assert "bsc: 1 addresses" in r.output


def test_one_limiter_per_provider_per_process(home: Path) -> None:
    rt = runtime.load()
    assert runtime.trongrid_limiter(rt) is runtime.trongrid_limiter(rt)
    assert runtime.hypersync_pacer(rt) is runtime.hypersync_pacer(rt)
    assert runtime.trongrid_limiter(rt) is not runtime.hypersync_pacer(rt)
