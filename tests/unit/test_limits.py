import httpx
import pytest
import respx

from amlcheck.config import Network
from amlcheck.net.http import Http, LimiterError, Mode, Provider, SourceError
from amlcheck.net.limits import BudgetPacer, TokenBucket
from tests.unit.fakes import FakeTime

URL = "https://indexer.test/query"


def budget(remaining: int, reset: int, cost: int = 1000) -> dict[str, str]:
    return {
        "x-ratelimit-remaining": str(remaining),
        "x-ratelimit-reset": str(reset),
        "x-ratelimit-cost": str(cost),
        "x-ratelimit-limit": "30000, 30000;w=60",
    }


def reset_header(resp: httpx.Response) -> float | None:
    value = resp.headers.get("x-ratelimit-reset")
    return float(value) if value is not None else None


async def test_token_bucket_spaces_requests() -> None:
    ft = FakeTime()
    tb = TokenBucket(10, monotonic=ft.monotonic, sleep=ft.sleep)
    for _ in range(4):
        await tb.acquire()
    assert ft.sleeps == [0.1, 0.1, 0.1]
    ft.now += 5  # idle time doesn't bank a burst
    await tb.acquire()
    await tb.acquire()
    assert ft.sleeps[-1] == 0.1


def test_token_bucket_rejects_zero_rate() -> None:
    with pytest.raises(ValueError, match="positive"):
        TokenBucket(0)


async def test_pacer_waits_for_window_when_budget_spent() -> None:
    ft = FakeTime()
    p = BudgetPacer(65, monotonic=ft.monotonic, sleep=ft.sleep)
    await p.acquire()  # nothing known yet: go
    p.observe(200, httpx.Headers(budget(1000, 20)))
    await p.acquire()  # 1000 left, costs 1000: go, now 0 left
    assert ft.sleeps == []
    await p.acquire()  # window empty: wait for reset
    assert ft.sleeps == [20.0]


async def test_pacer_refuses_wait_past_limit() -> None:
    ft = FakeTime()
    p = BudgetPacer(65, monotonic=ft.monotonic, sleep=ft.sleep)
    p.observe(200, httpx.Headers(budget(0, 66)))
    with pytest.raises(LimiterError, match="next window in 66 s"):
        await p.acquire()
    p.observe(200, httpx.Headers(budget(0, 65)))
    await p.acquire()
    assert ft.sleeps == [65.0]


async def test_pacer_ignores_zero_cost_header() -> None:
    ft = FakeTime()
    p = BudgetPacer(65, monotonic=ft.monotonic, sleep=ft.sleep)
    p.observe(200, httpx.Headers(budget(1500, 30, cost=1000)))
    p.observe(200, httpx.Headers(budget(1500, 30, cost=0)))
    await p.acquire()  # 1500 ≥ 1000
    await p.acquire()  # 500 < 1000 → wait
    assert ft.sleeps == [30.0]


# AT-06: indexer answers 429 with budget headers → pacer waits for the window and succeeds;
# wait > 65 s → error.
@respx.mock
async def test_at06_429_with_budget_headers_waits_then_succeeds() -> None:
    ft = FakeTime()
    pacer = BudgetPacer(65, monotonic=ft.monotonic, sleep=ft.sleep)
    hs = Provider("hypersync", limiter=pacer, refusal_wait=reset_header, refusal_is_pacing=True)
    respx.post(URL).mock(
        side_effect=[
            httpx.Response(429, headers=budget(0, 43)),
            httpx.Response(200, json={"ok": True}, headers=budget(29000, 60)),
        ]
    )
    async with httpx.AsyncClient() as c:
        r = await Http(c, Network(), mode=Mode.CHECK, sleep=ft.sleep).request(hs, "POST", URL)
    assert r.json() == {"ok": True}
    assert ft.sleeps == [43.0]


@respx.mock
async def test_at06_wait_over_65_s_is_error() -> None:
    ft = FakeTime()
    pacer = BudgetPacer(65, monotonic=ft.monotonic, sleep=ft.sleep)
    hs = Provider("hypersync", limiter=pacer, refusal_wait=reset_header, refusal_is_pacing=True)
    respx.post(URL).respond(429, headers=budget(0, 70))
    async with httpx.AsyncClient() as c:
        with pytest.raises(SourceError, match="hypersync: rate limited; would wait 70 s"):
            await Http(c, Network(), mode=Mode.CHECK, sleep=ft.sleep).request(hs, "POST", URL)
    assert ft.sleeps == []


@respx.mock
async def test_pacer_wait_too_long_before_sending_is_source_error() -> None:
    ft = FakeTime()
    pacer = BudgetPacer(65, monotonic=ft.monotonic, sleep=ft.sleep)
    pacer.observe(200, httpx.Headers(budget(0, 90)))
    route = respx.post(URL).respond(200)
    hs = Provider("hypersync", limiter=pacer)
    async with httpx.AsyncClient() as c:
        with pytest.raises(SourceError, match="hypersync: budget spent; next window in 90 s"):
            await Http(c, Network(), mode=Mode.CHECK, sleep=ft.sleep).request(hs, "POST", URL)
    assert route.call_count == 0
