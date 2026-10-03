import copy
import json
import sqlite3
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from amlcheck.config import Freshness, Network, Tron
from amlcheck.core.address import detect
from amlcheck.core.clock import fixed, from_ms, to_db
from amlcheck.core.models import Severity, SourceStatus
from amlcheck.net.http import Http, Mode, SourceError
from amlcheck.screening.tron_freeze import (
    TronBlacklistSource,
    TronFreezeIndex,
    TronFreezeSource,
    TronTether,
)
from amlcheck.storage.db import open_db
from tests.unit.fakes import FakeTime

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "trongrid"
API = "https://api.trongrid.io"
C = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
EVENTS_URL = f"{API}/v1/contracts/{C}/events"
FROZEN = "TNHrhtVnRMRaTJFRsqLUqSytCKsavXJJaa"  # AddedBlackList fixture user
HEAD_MS = json.loads((FIX / "walletsolidity_getnowblock.json").read_text())["block_header"][
    "raw_data"
]["timestamp"]
NOW = from_ms(HEAD_MS) + timedelta(minutes=2)


def fx(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIX / name).read_text())
    return data


def page(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"data": rows, "success": True, "meta": {"page_size": len(rows)}}


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as c:
        yield c


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / "a.db")


def parts(client: httpx.AsyncClient, conn: sqlite3.Connection, now: datetime = NOW):  # type: ignore[no-untyped-def]
    http = Http(client, Network(), mode=Mode.CHECK, sleep=FakeTime().sleep)
    tether = TronTether(http, Tron(), api_key="k", limiter=None)
    index = TronFreezeIndex(tether, conn, clock=fixed(now))
    return tether, index, TronFreezeSource(index, Freshness(), clock=fixed(now))


def mock_chain(
    events: dict[str, list[dict[str, Any]]], *, deprecated: bool = False, listed: bool = False
) -> respx.Route:
    def constant(request: httpx.Request) -> httpx.Response:
        sel = json.loads(request.content)["function_selector"]
        if sel == "deprecated()":
            data = fx("constant_deprecated_false.json")
            if deprecated:
                data["constant_result"] = ["0" * 63 + "1"]
            return httpx.Response(200, json=data)
        name = "constant_isblacklisted_true.json" if listed else "constant_isblacklisted_false.json"
        return httpx.Response(200, json=fx(name))

    respx.post(f"{API}/wallet/triggerconstantcontract").mock(side_effect=constant)
    respx.post(f"{API}/walletsolidity/getnowblock").respond(
        json=fx("walletsolidity_getnowblock.json")
    )
    return respx.get(EVENTS_URL).mock(
        side_effect=lambda r: httpx.Response(
            200, json=page(events.get(r.url.params["event_name"], []))
        )
    )


def ev(name: str, user_hex: str, ts_ms: int, tx: str, **result: str) -> dict[str, Any]:
    base = {
        "AddedBlackList": "events_added_blacklist.json",
        "RemovedBlackList": "events_removed_blacklist.json",
        "DestroyedBlackFunds": "events_destroyed_black_funds.json",
    }[name]
    row: dict[str, Any] = copy.deepcopy(fx(base)["data"][0])
    key = "_blackListedUser" if name == "DestroyedBlackFunds" else "_user"
    row["result"] = {key: user_hex, **result}
    row.update(block_timestamp=ts_ms, transaction_id=tx, block_number=ts_ms // 3000)
    return row


USER_HEX = "0x8728786c0786a671a3e027008805bc309ef19190"  # = FROZEN


@respx.mock
async def test_first_sync_stores_real_events(
    client: httpx.AsyncClient, conn: sqlite3.Connection
) -> None:
    mock_chain(
        {
            "AddedBlackList": fx("events_added_blacklist.json")["data"],
            "DestroyedBlackFunds": fx("events_destroyed_black_funds.json")["data"],
        }
    )
    _, index, _ = parts(client, conn)
    r = await index.refresh()
    assert r.new_events == 5
    assert index.last_synced() == from_ms(HEAD_MS)
    (added,) = [e for e in index.events_for(FROZEN) if e["event"] == "AddedBlackList"]
    assert added["tx_hash"].startswith("d68e3510")
    destroyed = conn.execute(
        "SELECT amount FROM issuer_events WHERE event_type = 'DestroyedBlackFunds' "
        "ORDER BY block DESC"
    ).fetchone()
    assert destroyed[0] == "36855.997408"
    again = await index.refresh()
    assert again.new_events == 0


@respx.mock
async def test_incremental_refresh_starts_at_last_head(
    client: httpx.AsyncClient, conn: sqlite3.Connection
) -> None:
    route = mock_chain({})
    _, index, _ = parts(client, conn)
    await index.refresh()
    assert "min_block_timestamp" not in route.calls[0].request.url.params
    await index.refresh()
    assert route.calls[-1].request.url.params["min_block_timestamp"] == str(HEAD_MS)


@respx.mock
async def test_events_after_head_wait_for_next_refresh(
    client: httpx.AsyncClient, conn: sqlite3.Connection
) -> None:
    mock_chain({"AddedBlackList": [ev("AddedBlackList", USER_HEX, HEAD_MS + 5_000, "tx-late")]})
    _, index, _ = parts(client, conn)
    assert (await index.refresh()).new_events == 0


# AT-17: latest event AddedBlackList → BLOCK; RemovedBlackList after it → REVIEW R-FRZ-02.
@pytest.mark.parametrize(
    ("events", "rule", "severity"),
    [
        (
            {"AddedBlackList": [ev("AddedBlackList", USER_HEX, HEAD_MS - 9_000, "a1")]},
            "R-FRZ-01",
            Severity.BLOCK,
        ),
        (
            {
                "AddedBlackList": [ev("AddedBlackList", USER_HEX, HEAD_MS - 9_000, "a1")],
                "RemovedBlackList": [ev("RemovedBlackList", USER_HEX, HEAD_MS - 6_000, "r1")],
            },
            "R-FRZ-02",
            Severity.REVIEW,
        ),
        (
            {
                "AddedBlackList": [
                    ev("AddedBlackList", USER_HEX, HEAD_MS - 9_000, "a1"),
                    ev("AddedBlackList", USER_HEX, HEAD_MS - 3_000, "a2"),
                ],
                "RemovedBlackList": [ev("RemovedBlackList", USER_HEX, HEAD_MS - 6_000, "r1")],
            },
            "R-FRZ-01",
            Severity.BLOCK,
        ),
        (
            {
                "AddedBlackList": [ev("AddedBlackList", USER_HEX, HEAD_MS - 9_000, "a1")],
                "RemovedBlackList": [ev("RemovedBlackList", USER_HEX, HEAD_MS - 6_000, "r1")],
                "DestroyedBlackFunds": [
                    ev("DestroyedBlackFunds", USER_HEX, HEAD_MS - 7_000, "d1", _balance="1500000")
                ],
            },
            "R-FRZ-01",
            Severity.BLOCK,
        ),
    ],
)
@respx.mock
async def test_at17_rules_from_index(
    client: httpx.AsyncClient,
    conn: sqlite3.Connection,
    events: dict[str, list[dict[str, Any]]],
    rule: str,
    severity: Severity,
) -> None:
    mock_chain(events)
    _, _, src = parts(client, conn)
    r = await src.check(detect(FROZEN))
    assert r.status is SourceStatus.OK
    (f,) = r.findings
    assert (f.rule_id, f.severity) == (rule, severity)
    assert f.evidence["chain"] == "tron"
    assert f.evidence["event"]["tx_hash"]


@respx.mock
async def test_clean_address(client: httpx.AsyncClient, conn: sqlite3.Connection) -> None:
    mock_chain({"AddedBlackList": [ev("AddedBlackList", USER_HEX, HEAD_MS - 9_000, "a1")]})
    _, _, src = parts(client, conn)
    r = await src.check(detect("TNPeeaaFB7K9cmo4uQpcU32zGK8G1NYqeL"))
    assert r.status is SourceStatus.OK
    assert r.findings == ()


# AT-18: deprecated() true → freeze index source error → INCOMPLETE.
@respx.mock
async def test_at18_deprecated_contract_is_error(
    client: httpx.AsyncClient, conn: sqlite3.Connection
) -> None:
    mock_chain({}, deprecated=True)
    _, _, src = parts(client, conn)
    r = await src.check(detect(FROZEN))
    assert r.status is SourceStatus.ERROR
    assert "deprecated()" in (r.detail or "")


# AT-13: the index can't be refreshed and lags > 60 min → stale (INCOMPLETE).
@pytest.mark.parametrize(("lag_min", "status"), [(59, SourceStatus.OK), (61, SourceStatus.STALE)])
@respx.mock
async def test_at13_lag_after_failed_refresh(
    client: httpx.AsyncClient, conn: sqlite3.Connection, lag_min: int, status: SourceStatus
) -> None:
    for key in ("AddedBlackList", "RemovedBlackList", "DestroyedBlackFunds"):
        conn.execute(
            "INSERT INTO index_state VALUES (?, 1, ?, ?)",
            (f"tron_freeze:{key}", to_db(NOW - timedelta(minutes=lag_min)), to_db(NOW)),
        )
    respx.post(f"{API}/wallet/triggerconstantcontract").respond(503)
    _, _, src = parts(client, conn)
    r = await src.check(detect(FROZEN))
    assert r.status is status
    assert "refresh failed" in (r.detail or "")


@respx.mock
async def test_never_synced_and_unreachable_is_stale(
    client: httpx.AsyncClient, conn: sqlite3.Connection
) -> None:
    respx.post(f"{API}/wallet/triggerconstantcontract").respond(503)
    _, _, src = parts(client, conn)
    r = await src.check(detect(FROZEN))
    assert r.status is SourceStatus.STALE
    assert "never synced" in (r.detail or "")


@pytest.mark.parametrize(("listed", "n"), [(True, 1), (False, 0)])
@respx.mock
async def test_is_blacklisted_spot_check(
    client: httpx.AsyncClient, conn: sqlite3.Connection, listed: bool, n: int
) -> None:
    route = mock_chain({}, listed=listed)
    tether, _, _ = parts(client, conn)
    r = await TronBlacklistSource(tether, clock=fixed(NOW)).check(detect(FROZEN))
    assert r.status is SourceStatus.OK
    assert len(r.findings) == n
    body = json.loads(route.calls.last.request.content) if route.calls else None
    assert body is None or body.get("event_name") is None


@respx.mock
async def test_is_blacklisted_param_and_error(
    client: httpx.AsyncClient, conn: sqlite3.Connection
) -> None:
    route = respx.post(f"{API}/wallet/triggerconstantcontract").respond(
        json=fx("constant_isblacklisted_true.json")
    )
    tether, _, _ = parts(client, conn)
    await TronBlacklistSource(tether, clock=fixed(NOW)).check(detect(FROZEN))
    sent = json.loads(route.calls.last.request.content)
    assert sent["function_selector"] == "isBlackListed(address)"
    assert sent["parameter"] == "0" * 24 + "8728786c0786a671a3e027008805bc309ef19190"
    respx.post(f"{API}/wallet/triggerconstantcontract").respond(503)
    r = await TronBlacklistSource(tether, clock=fixed(NOW)).check(detect(FROZEN))
    assert r.status is SourceStatus.ERROR


@respx.mock
async def test_repeated_event_page_link_is_an_error(
    client: httpx.AsyncClient, conn: sqlite3.Connection
) -> None:
    respx.get(url__startswith=EVENTS_URL).respond(json=fx("events_added_blacklist.json"))
    tether, _, _ = parts(client, conn)
    with pytest.raises(SourceError, match="paging does not advance"):
        await tether.events("AddedBlackList", None)
