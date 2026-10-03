import json
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import respx

from amlcheck.chain.bsc import BscContractLookup
from amlcheck.chain.cache import ContractCache
from amlcheck.chain.tron import TronContractLookup
from amlcheck.config import Bsc, Network, Tron
from amlcheck.core.models import Chain
from amlcheck.net.http import Http, Mode, SourceError
from amlcheck.storage.db import open_db
from tests.unit.fakes import FakeTime

FIX = Path(__file__).resolve().parents[1] / "fixtures"
TG = "https://api.trongrid.io/wallet/getcontract"
RPC = Bsc().rpc_url


def fx(path: str) -> object:
    return json.loads((FIX / path).read_text())


@pytest.fixture
async def http() -> AsyncIterator[Http]:
    async with httpx.AsyncClient() as c:
        yield Http(c, Network(), mode=Mode.CHECK, sleep=FakeTime().sleep)


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        ("trongrid/getcontract_contract.json", True),
        ("trongrid/getcontract_created_by_contract.json", True),  # no bytecode, still a contract
        ("trongrid/getcontract_wallet.json", False),
        ("trongrid/getcontract_never_used.json", False),
    ],
)
@respx.mock
async def test_tron_lookup(http: Http, fixture: str, expected: bool) -> None:
    route = respx.post(TG).respond(json=fx(fixture))
    lookup = TronContractLookup(http, Tron(), api_key="k", limiter=None)
    assert await lookup.is_contract("TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t") is expected
    assert json.loads(route.calls.last.request.content) == {
        "value": "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
        "visible": True,
    }


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        ("bsc_rpc/eth_getcode_usdt.json", True),
        ("bsc_rpc/eth_getcode_wallet.json", False),
        ("bsc_rpc/eth_getcode_never_used.json", False),
    ],
)
@respx.mock
async def test_bsc_lookup(http: Http, fixture: str, expected: bool) -> None:
    route = respx.post(RPC).respond(json=fx(fixture))
    assert (
        await BscContractLookup(http, Bsc(), limiter=None).is_contract("0x" + "ab" * 20) is expected
    )
    sent = json.loads(route.calls.last.request.content)
    assert (sent["method"], sent["params"]) == ("eth_getCode", ["0x" + "ab" * 20, "latest"])


@respx.mock
async def test_bsc_rpc_error_is_source_error(http: Http) -> None:
    respx.post(RPC).respond(
        json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32005, "message": "limit"}}
    )
    with pytest.raises(SourceError, match="eth_getCode failed"):
        await BscContractLookup(http, Bsc(), limiter=None).is_contract("0x" + "ab" * 20)


@respx.mock
async def test_cache_asks_once_forever(http: Http, tmp_path: Path) -> None:
    conn: sqlite3.Connection = open_db(tmp_path / "a.db")
    route = respx.post(RPC).respond(json=fx("bsc_rpc/eth_getcode_usdt.json"))
    cache = ContractCache(conn, {Chain.BSC: BscContractLookup(http, Bsc(), limiter=None)})
    assert await cache.is_contract(Chain.BSC, "0x55d398326f99059ff775485246999027b3197955")
    assert await cache.is_contract(Chain.BSC, "0x55d398326f99059ff775485246999027b3197955")
    assert route.call_count == 1
    row = conn.execute("SELECT is_contract, source FROM contracts").fetchone()
    assert row == (1, "BscContractLookup")


@respx.mock
async def test_failed_lookup_is_not_cached(http: Http, tmp_path: Path) -> None:
    conn = open_db(tmp_path / "a.db")
    respx.post(RPC).respond(500)
    cache = ContractCache(conn, {Chain.BSC: BscContractLookup(http, Bsc(), limiter=None)})
    with pytest.raises(SourceError):
        await cache.is_contract(Chain.BSC, "0x" + "ab" * 20)
    assert conn.execute("SELECT count(*) FROM contracts").fetchone()[0] == 0
