import copy
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from amlcheck.chain.base import canonical_amount
from amlcheck.chain.tron import TronGridSource, suspension_seconds
from amlcheck.config import Network, Tron
from amlcheck.core.clock import fixed, from_ms
from amlcheck.net.http import Http, Mode, SourceError
from tests.unit.fakes import FakeTime

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "trongrid"
API = "https://api.trongrid.io"
NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)
LONG_AGO = datetime(2020, 1, 1, tzinfo=UTC)
HOT = "TV6MuMXfmLbBqPZvBHdwFsDnQeVfnmiuSi"


def fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIX / name).read_text())
    return data


def trc20(address: str) -> str:
    return f"{API}/v1/accounts/{address}/transactions/trc20"


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as c:
        yield c


def source(client: httpx.AsyncClient, key: str | None = "k-123") -> TronGridSource:
    http = Http(client, Network(), mode=Mode.CHECK, sleep=FakeTime().sleep)
    return TronGridSource(http, Tron(), api_key=key, limiter=None, clock=fixed(NOW))


def one_page(name: str) -> dict[str, Any]:
    data = fixture(name)
    data.setdefault("meta", {}).pop("links", None)
    return data


@respx.mock
async def test_reads_page_newest_first(client: httpx.AsyncClient) -> None:
    route = respx.get(trc20(HOT)).respond(json=one_page("trc20_transfers_desc.json"))
    h = await source(client).fetch(HOT, LONG_AGO, None, 100, first_activity=False)
    assert h.complete
    assert h.zero_value == 0
    assert h.first_activity is None
    assert len(h.transfers) == 5
    first = h.transfers[0]
    assert first.time == from_ms(1789220535000)
    assert (first.recipient, canonical_amount(first.amount), first.block) == (HOT, "5", None)
    assert [t.time for t in h.transfers] == sorted((t.time for t in h.transfers), reverse=True)
    q = route.calls.last.request.url.params
    assert q["order_by"] == "block_timestamp,desc"
    assert q["only_confirmed"] == "true"
    assert q["contract_address"] == "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
    assert q["min_timestamp"] == str(int(LONG_AGO.timestamp() * 1000))
    assert q["max_timestamp"] == str(int(NOW.timestamp() * 1000))
    assert route.calls.last.request.headers["TRON-PRO-API-KEY"] == "k-123"
    assert h.until == NOW


@respx.mock
async def test_no_key_no_header(client: httpx.AsyncClient) -> None:
    route = respx.get(trc20(HOT)).respond(json=one_page("trc20_transfers_empty.json"))
    await source(client, key=None).fetch(HOT, LONG_AGO, NOW, 10, first_activity=False)
    assert "TRON-PRO-API-KEY" not in route.calls.last.request.headers


@respx.mock
async def test_empty_answer(client: httpx.AsyncClient) -> None:
    respx.get(trc20(HOT)).respond(json=fixture("trc20_transfers_empty.json"))
    h = await source(client).fetch(HOT, LONG_AGO, NOW, 10, first_activity=False)
    assert h.transfers == ()
    assert h.complete
    assert h.zero_value == 0


@respx.mock
async def test_follows_next_link(client: httpx.AsyncClient) -> None:
    page1 = fixture("trc20_transfers_desc.json")
    next_url = page1["meta"]["links"]["next"]
    page2 = copy.deepcopy(page1)
    page2["data"] = [dict(r, transaction_id="aa" + r["transaction_id"][2:]) for r in page1["data"]]
    page2["meta"].pop("links")
    respx.get(trc20(HOT)).mock(
        side_effect=[httpx.Response(200, json=page1), httpx.Response(200, json=page2)]
    )
    h = await source(client).fetch(HOT, LONG_AGO, NOW, 100, first_activity=False)
    assert len(h.transfers) == 10
    assert h.complete
    assert next_url.startswith(API)


# AT-04 at the source: more than the limit → newest `limit`, incomplete.
@respx.mock
async def test_limit_marks_incomplete(client: httpx.AsyncClient) -> None:
    route = respx.get(trc20(HOT)).respond(json=fixture("trc20_transfers_desc.json"))
    h = await source(client).fetch(HOT, LONG_AGO, NOW, 3, first_activity=False)
    assert not h.complete
    assert [t.time for t in h.transfers] == [
        from_ms(1789220535000),
        from_ms(1786985457000),
        from_ms(1765889889000),
    ]
    assert route.call_count == 1  # stops as soon as it knows there are more


# AT-05 at the source: 0-value rows dropped and counted.
@respx.mock
async def test_zero_value_dropped_and_counted(client: httpx.AsyncClient) -> None:
    page = one_page("trc20_transfers_desc.json")
    page["data"][1]["value"] = "0"
    page["data"][3]["value"] = "0"
    respx.get(trc20(HOT)).respond(json=page)
    h = await source(client).fetch(HOT, LONG_AGO, NOW, 100, first_activity=False)
    assert len(h.transfers) == 3
    assert h.zero_value == 2


@respx.mock
async def test_rows_outside_window_ignored(client: httpx.AsyncClient) -> None:
    respx.get(trc20(HOT)).respond(json=one_page("trc20_transfers_desc.json"))
    since, until = from_ms(1750520838000), from_ms(1786985457000)  # inclusive both ends
    h = await source(client).fetch(HOT, since, until, 100, first_activity=False)
    assert [t.time for t in h.transfers] == [until, from_ms(1765889889000), since]


# D-030: the sender's and the recipient's views give the same key for the shared transfer.
@respx.mock
async def test_d030_same_key_from_both_sides(client: httpx.AsyncClient) -> None:
    sender, recipient = "TZ53RkfpZKtGJpbrb16YjycT3i8t7HNp11", "TBTrPYEiB1GafBdWygFBa1gBB7DNZGRhqp"
    respx.get(trc20(sender)).respond(json=one_page("trc20_two_in_one_tx_sender_view.json"))
    respx.get(trc20(recipient)).respond(json=one_page("trc20_two_in_one_tx_recipient_view.json"))
    s = await source(client).fetch(sender, LONG_AGO, NOW, 10, first_activity=False)
    r = await source(client).fetch(recipient, LONG_AGO, NOW, 10, first_activity=False)
    assert len(s.transfers) == 2
    assert len(r.transfers) == 1
    assert r.transfers[0] in s.transfers
    assert {t.idx for t in s.transfers} == {0}


@respx.mock
async def test_d030_identical_rows_get_distinct_idx(client: httpx.AsyncClient) -> None:
    page = one_page("trc20_two_in_one_tx_sender_view.json")
    page["data"] = [page["data"][1], dict(page["data"][1]), page["data"][0]]
    respx.get(trc20("TZ53RkfpZKtGJpbrb16YjycT3i8t7HNp11")).respond(json=page)
    h = await source(client).fetch(
        "TZ53RkfpZKtGJpbrb16YjycT3i8t7HNp11", LONG_AGO, NOW, 10, first_activity=False
    )
    fee = sorted(t.idx for t in h.transfers if canonical_amount(t.amount) == "1.5")
    assert fee == [0, 1]


@respx.mock
async def test_next_link_elsewhere_refused(client: httpx.AsyncClient) -> None:
    page = fixture("trc20_transfers_desc.json")
    page["meta"]["links"]["next"] = "https://evil.test/steal"
    respx.get(trc20(HOT)).respond(json=page)
    with pytest.raises(SourceError, match="points elsewhere"):
        await source(client).fetch(HOT, LONG_AGO, NOW, 100, first_activity=False)


@respx.mock
async def test_unsuccessful_or_odd_answers_are_errors(client: httpx.AsyncClient) -> None:
    respx.get(trc20(HOT)).respond(json={"success": False, "error": "bad"})
    with pytest.raises(SourceError, match="not successful: bad"):
        await source(client).fetch(HOT, LONG_AGO, NOW, 10, first_activity=False)
    respx.get(trc20(HOT)).respond(json={"data": [{"type": "Transfer"}]})
    with pytest.raises(SourceError, match="unexpected transfer row"):
        await source(client).fetch(HOT, LONG_AGO, NOW, 10, first_activity=False)
    respx.get(trc20(HOT)).respond(text="<html>")
    with pytest.raises(SourceError, match="not JSON"):
        await source(client).fetch(HOT, LONG_AGO, NOW, 10, first_activity=False)


# VS-05 / D-036: the keyed refusal, read from the real answer.
def test_suspension_seconds_from_real_answer() -> None:
    body = fixture("rate_limited_with_key.json")["body"]
    assert suspension_seconds(httpx.Response(429, text=body)) == 30.0
    no_key = fixture("rate_limited_429_no_key.json")["body"]
    assert suspension_seconds(httpx.Response(429, text=no_key)) == 5.0
    assert suspension_seconds(httpx.Response(403, text="")) == 30.0


@respx.mock
async def test_refusal_in_check_mode_is_error(client: httpx.AsyncClient) -> None:
    respx.get(trc20(HOT)).respond(429, text=fixture("rate_limited_with_key.json")["body"])
    with pytest.raises(SourceError, match="suspended for 30 s"):
        await source(client).fetch(HOT, LONG_AGO, NOW, 10, first_activity=False)


# VS-10 / methodology §3.1: first activity = earliest of create_time and first USDT transfer.
@pytest.mark.parametrize(
    ("account", "first_row_ms", "expected_ms"),
    [
        ("getaccount_active.json", 1789014057000, 1789014000000),  # activation first
        ("getaccount_active.json", 1700000000000, 1700000000000),  # USDT before activation
        ("getaccount_contract_no_create_time.json", 1751428875000, 1751428875000),
        ("getaccount_never_activated.json", None, None),
    ],
)
@respx.mock
async def test_first_activity(
    client: httpx.AsyncClient, account: str, first_row_ms: int | None, expected_ms: int | None
) -> None:
    acct = fixture(account)
    if account == "getaccount_active.json":
        acct["create_time"] = 1789014000000
    respx.post(f"{API}/wallet/getaccount").respond(json=acct)
    rows = [] if first_row_ms is None else [dict(fixture("trc20_transfers_desc.json")["data"][0])]
    if rows:
        rows[0]["block_timestamp"] = first_row_ms
    respx.get(trc20(HOT)).mock(
        side_effect=[
            httpx.Response(200, json=one_page("trc20_transfers_empty.json")),
            httpx.Response(200, json={"data": rows, "success": True, "meta": {}}),
        ]
    )
    h = await source(client).fetch(HOT, LONG_AGO, NOW, 10, first_activity=True)
    assert h.first_activity == (from_ms(expected_ms) if expected_ms else None)


@respx.mock
async def test_repeated_page_link_is_an_error_not_a_hang(client: httpx.AsyncClient) -> None:
    respx.get(url__startswith=trc20(HOT)).respond(json=fixture("trc20_transfers_desc.json"))
    with pytest.raises(SourceError, match="paging does not advance"):
        await source(client).fetch(HOT, LONG_AGO, NOW, 10_000, first_activity=False)
