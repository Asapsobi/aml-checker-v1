import json
import random
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from amlcheck.chain.base import Transfer, amount_from_units, canonical_amount, newest_first
from amlcheck.chain.bsc import HyperSyncSource
from amlcheck.config import Bsc, Network
from amlcheck.core.clock import fixed
from amlcheck.core.models import Chain
from amlcheck.net.http import Http, Mode, SourceError
from tests.unit.fake_hypersync import OTHER_TOKEN, FakeHyperSync, Log, Tx
from tests.unit.fakes import FakeTime

HOST = "https://bsc.hypersync.xyz"
FALLBACK = "https://56.hypersync.xyz"
ME = "0x" + "11" * 20
FIX = Path(__file__).resolve().parents[1] / "fixtures" / "hypersync"


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as c:
        yield c


def source(client: httpx.AsyncClient, fake: FakeHyperSync, lag_s: int = 5) -> HyperSyncSource:
    http = Http(client, Network(), mode=Mode.CHECK, sleep=FakeTime().sleep)
    now = fake.head_time() + timedelta(seconds=lag_s)
    return HyperSyncSource(http, Bsc(), token="tok", pacer=None, clock=fixed(now))


def peer(n: int) -> str:
    return "0x" + format(n, "040x")


def brute(
    fake: FakeHyperSync, addr: str, since: datetime, until: datetime, limit: int
) -> tuple[tuple[Transfer, ...], bool]:
    """The expected answer, straight from the fake chain's logs."""
    hits = {
        (log.tx, log.index): Transfer(
            Chain.BSC,
            log.tx,
            log.index,
            log.block,
            fake.time(log.block),
            log.sender,
            log.recipient,
            amount_from_units(log.units, 18),
        )
        for log in fake.logs
        if log.token != OTHER_TOKEN
        and addr in (log.sender, log.recipient)
        and since <= fake.time(log.block) <= until
        and log.units > 0
    }
    ordered = newest_first(hits.values())
    return ordered[:limit], len(ordered) <= limit


def wallet(fake: FakeHyperSync, blocks: list[int], seed: int = 1) -> None:
    rng = random.Random(seed)
    for i, b in enumerate(sorted(blocks)):
        out = rng.random() < 0.5
        other = peer(rng.randrange(1, 50))
        fake.logs.append(
            Log(
                b,
                rng.randrange(0, 200),
                f"0x{i:064x}",
                ME if out else other,
                other if out else ME,
                rng.randrange(1, 10**21),
            )
        )


@respx.mock
async def test_quiet_wallet_one_query(client: httpx.AsyncClient) -> None:
    fake = FakeHyperSync(page_logs=1000)
    wallet(fake, [20_000, 50_000, 99_000])
    respx.route(host="bsc.hypersync.xyz").mock(side_effect=fake.handler)
    since = fake.time(15_000)
    h = await source(client, fake).fetch(ME, since, None, 100, first_activity=False)
    assert h.complete
    assert [t.block for t in h.transfers] == [99_000, 50_000, 20_000]
    assert fake.queries == 1
    assert h.transfers[0].time == fake.time(99_000)


# AT-04 at the source, and the backwards read of a busy wallet.
@respx.mock
async def test_busy_wallet_newest_limit_incomplete(client: httpx.AsyncClient) -> None:
    fake = FakeHyperSync(page_logs=5)
    wallet(fake, list(range(11_000, 99_000, 40)))  # 2,200 transfers
    respx.route(host="bsc.hypersync.xyz").mock(side_effect=fake.handler)
    since = fake.time(10_500)
    h = await source(client, fake).fetch(ME, since, None, 30, first_activity=False)
    want, _ = brute(fake, ME, since, fake.head_time() + timedelta(seconds=5), 30)
    assert not h.complete
    assert h.transfers == want
    assert fake.queries < 20  # not the ~440 pages a forward read of everything would take


# VS-07: a new, busy wallet at the end of a long window, half its logs 0-value spam. The first page
# proves the blocks before its end are empty, so the read must not walk them again.
@respx.mock
async def test_new_busy_wallet_does_not_rescan_empty_blocks(client: httpx.AsyncClient) -> None:
    fake = FakeHyperSync(page_logs=40)
    wallet(fake, list(range(97_000, 99_900, 50)))  # 58 real transfers, all near the head
    fake.logs += [Log(97_010 + 50 * i, 300, f"0xd{i:063x}", peer(9), ME, 0) for i in range(58)]
    respx.route(host="bsc.hypersync.xyz").mock(side_effect=fake.handler)
    since = fake.time(12_000)  # a window of ~88,000 blocks, nearly all empty
    h = await source(client, fake).fetch(ME, since, None, 100, first_activity=False)
    want, complete = brute(fake, ME, since, fake.head_time() + timedelta(seconds=5), 100)
    assert h.transfers == want
    assert h.complete is complete is True
    assert h.zero_value == 58
    assert fake.queries <= 5  # was ~45: 2,000-block chunks down to the window's start


# AT-05 at the source.
@respx.mock
async def test_zero_value_dropped_and_counted(client: httpx.AsyncClient) -> None:
    fake = FakeHyperSync(page_logs=1000)
    fake.logs += [
        Log(60_000, 1, "0xa", peer(5), ME, 0),
        Log(60_001, 2, "0xb", peer(6), ME, 7 * 10**18),
        Log(60_002, 3, "0xc", ME, peer(7), 0),
    ]
    respx.route(host="bsc.hypersync.xyz").mock(side_effect=fake.handler)
    h = await source(client, fake).fetch(ME, fake.time(50_000), None, 10, first_activity=False)
    assert [canonical_amount(t.amount) for t in h.transfers] == ["7"]
    assert h.zero_value == 2


@respx.mock
async def test_self_transfer_counted_once_and_other_tokens_ignored(
    client: httpx.AsyncClient,
) -> None:
    fake = FakeHyperSync(page_logs=1000)
    fake.logs += [
        Log(70_000, 4, "0xd", ME, ME, 10**18),
        Log(70_001, 1, "0xe", ME, peer(2), 5, OTHER_TOKEN),
    ]
    respx.route(host="bsc.hypersync.xyz").mock(side_effect=fake.handler)
    h = await source(client, fake).fetch(ME, fake.time(60_000), None, 10, first_activity=False)
    assert len(h.transfers) == 1
    assert h.transfers[0].sender == h.transfers[0].recipient == ME


@settings(
    max_examples=60, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(
    seed=st.integers(0, 10_000),
    n=st.integers(0, 300),
    page=st.integers(1, 40),
    a=st.integers(0, 100_000),
    b=st.integers(0, 100_000),
    limit=st.integers(1, 120),
    open_end=st.booleans(),
)
async def test_property_matches_brute_force(
    seed: int, n: int, page: int, a: int, b: int, limit: int, open_end: bool
) -> None:
    """Whatever the density, window and limit: the newest `limit` in the window, and `complete`
    exactly when nothing was left out (covers both block bounds, incl. the slow early blocks)."""
    fake = FakeHyperSync(page_logs=page)
    rng = random.Random(seed)
    wallet(fake, [rng.randrange(0, fake.head + 1) for _ in range(n)], seed)
    lo_b, hi_b = sorted((a, b))
    since = fake.time(lo_b)
    until = None if open_end else fake.time(hi_b)
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)) as c:
        http = Http(c, Network(), mode=Mode.CHECK, sleep=FakeTime().sleep)
        now = fake.head_time() + timedelta(seconds=5)
        src = HyperSyncSource(
            http, Bsc(hypersync_url="https://h"), token="t", pacer=None, clock=fixed(now)
        )
        h = await src.fetch(ME, since, until, limit, first_activity=False)
    want, complete = brute(fake, ME, since, until or now, limit)
    assert h.transfers == want
    assert h.complete == complete


@respx.mock
async def test_fallback_host_when_main_unreachable(client: httpx.AsyncClient) -> None:
    fake = FakeHyperSync(page_logs=1000)
    wallet(fake, [80_000])
    respx.route(host="bsc.hypersync.xyz").mock(side_effect=httpx.ConnectError("dns"))
    respx.route(host="56.hypersync.xyz").mock(side_effect=fake.handler)
    h = await source(client, fake).fetch(ME, fake.time(70_000), None, 10, first_activity=False)
    assert len(h.transfers) == 1


@respx.mock
async def test_no_progress_is_an_error(client: httpx.AsyncClient) -> None:
    fake = FakeHyperSync()
    respx.get(f"{HOST}/height").respond(json={"height": fake.head})
    respx.post(f"{HOST}/query").respond(json={"data": [], "next_block": 0, "archive_height": 9})
    with pytest.raises(SourceError, match="no progress"):
        await source(client, fake).fetch(ME, fake.time(50_000), None, 10, first_activity=False)


@respx.mock
async def test_missing_block_timestamp_is_an_error(client: httpx.AsyncClient) -> None:
    fake = FakeHyperSync()
    respx.get(f"{HOST}/height").respond(json={"height": fake.head})
    log = {
        "block_number": 5,
        "log_index": 0,
        "transaction_hash": "0x1",
        "topic1": "0x" + "0" * 64,
        "topic2": "0x" + "0" * 64,
        "data": "0x01",
    }
    respx.post(f"{HOST}/query").respond(
        json={"data": [{"logs": [log], "blocks": []}], "next_block": fake.head + 1}
    )
    with pytest.raises(SourceError, match="lacks timestamps"):
        await source(client, fake).fetch(ME, fake.time(1), None, 10, first_activity=False)


def test_token_required() -> None:
    http = Http(httpx.AsyncClient(), Network(), mode=Mode.CHECK)
    with pytest.raises(SourceError, match="AMLCHECK_HYPERSYNC_TOKEN"):
        HyperSyncSource(http, Bsc(), token="", pacer=None)


# VS-10: first activity = first tx sent/received or first Transfer log naming the address.
@pytest.mark.parametrize(
    ("txs", "logs", "expected"),
    [
        ([Tx(30_000, ME, peer(1))], [Log(40_000, 0, "0x1", peer(2), ME, 5)], 30_000),
        ([Tx(45_000, peer(1), ME)], [Log(40_000, 0, "0x1", peer(2), ME, 5, OTHER_TOKEN)], 40_000),
        ([], [], None),
    ],
)
@respx.mock
async def test_first_activity(
    client: httpx.AsyncClient, txs: list[Tx], logs: list[Log], expected: int | None
) -> None:
    fake = FakeHyperSync(page_logs=1)
    fake.txs, fake.logs = txs, logs
    respx.route(host="bsc.hypersync.xyz").mock(side_effect=fake.handler)
    h = await source(client, fake).fetch(ME, fake.time(99_000), None, 10, first_activity=True)
    assert h.first_activity == (fake.time(expected) if expected is not None else None)


# The real VS-06 answer parses: hex timestamps, padded topics, 18-decimal amounts.
@respx.mock
async def test_real_answer_shape(client: httpx.AsyncClient) -> None:
    fix = json.loads((FIX / "query_usdt_transfers_by_address.json").read_text())
    resp = fix["response"]
    head = fix["request"]["to_block"]
    resp["next_block"] = head + 1  # the source asks up to head + 1 (exclusive)
    respx.get(f"{HOST}/height").respond(json={"height": head})
    respx.post(f"{HOST}/query").respond(json=resp, headers=fix["headers"])
    now = datetime.fromtimestamp(int("0x6abe3f81", 16) + 30, UTC)
    http = Http(client, Network(), mode=Mode.CHECK, sleep=FakeTime().sleep)
    src = HyperSyncSource(http, Bsc(), token="t", pacer=None, clock=fixed(now))
    addr = "0x8894e0a0c962cb723c1976a4421c95949be2d4e3"
    h = await src.fetch(addr, now - timedelta(hours=1), None, 100, first_activity=False)
    assert len(h.transfers) == 4
    t = next(x for x in h.transfers if x.tx_hash.startswith("0xa240feb3"))
    assert (t.block, t.idx, t.sender, canonical_amount(t.amount)) == (125090031, 401, addr, "25.3")
    assert t.recipient == "0x563964848e39eeb97269ab3be3f50cb8f3a67ca6"
    assert t.time == datetime.fromtimestamp(0x6ABE3F7B, UTC)
