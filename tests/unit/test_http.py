from collections.abc import AsyncIterator

import httpx
import pytest
import respx

from amlcheck.config import Network
from amlcheck.net.http import Http, Mode, Provider, SourceError, Unreachable
from tests.unit.fakes import FakeTime

URL = "https://provider.test/x"


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as c:
        yield c


def make(client: httpx.AsyncClient, ft: FakeTime, mode: Mode = Mode.CHECK) -> Http:
    return Http(client, Network(), mode=mode, sleep=ft.sleep)


def suspended(resp: httpx.Response) -> float | None:
    return 30.0


PLAIN = Provider("plain")
TRON_LIKE = Provider("tron", refusal_statuses=frozenset({429, 403}), refusal_wait=suspended)


@respx.mock
async def test_ok_passes_through(client: httpx.AsyncClient) -> None:
    route = respx.get(URL).respond(200, json={"a": 1})
    ft = FakeTime()
    r = await make(client, ft).request(PLAIN, "GET", URL, params={"q": 1}, headers={"K": "v"})
    assert r.json() == {"a": 1}
    assert route.calls.last.request.url.params["q"] == "1"
    assert route.calls.last.request.headers["K"] == "v"
    assert ft.sleeps == []


# AT-14: 429 with Retry-After: 3, then OK → waits, succeeds. Retry-After: 30 → error.
@respx.mock
async def test_retry_after_short_is_waited_once(client: httpx.AsyncClient) -> None:
    respx.get(URL).mock(
        side_effect=[httpx.Response(429, headers={"Retry-After": "3"}), httpx.Response(200)]
    )
    ft = FakeTime()
    assert (await make(client, ft).request(PLAIN, "GET", URL)).status_code == 200
    assert ft.sleeps == [3.0]


@respx.mock
async def test_retry_after_at_limit_is_waited_just_past_is_error(client: httpx.AsyncClient) -> None:
    respx.get(URL).mock(
        side_effect=[httpx.Response(429, headers={"Retry-After": "10"}), httpx.Response(200)]
    )
    assert (await make(client, FakeTime()).request(PLAIN, "GET", URL)).status_code == 200
    respx.get(URL).mock(side_effect=[httpx.Response(429, headers={"Retry-After": "11"})])
    with pytest.raises(SourceError, match="asked to wait 11 s"):
        await make(client, FakeTime()).request(PLAIN, "GET", URL)


@respx.mock
async def test_retry_after_long_is_error(client: httpx.AsyncClient) -> None:
    respx.get(URL).respond(429, headers={"Retry-After": "30"})
    ft = FakeTime()
    with pytest.raises(SourceError, match="plain: rate limited; asked to wait 30 s"):
        await make(client, ft).request(PLAIN, "GET", URL)
    assert ft.sleeps == []


@respx.mock
async def test_retry_after_only_once(client: httpx.AsyncClient) -> None:
    respx.get(URL).respond(429, headers={"Retry-After": "2"})
    ft = FakeTime()
    with pytest.raises(SourceError, match="again after waiting"):
        await make(client, ft).request(PLAIN, "GET", URL)
    assert ft.sleeps == [2.0]


@respx.mock
async def test_retry_after_http_date_not_waited(client: httpx.AsyncClient) -> None:
    respx.get(URL).respond(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})
    with pytest.raises(SourceError, match="asked to wait"):
        await make(client, FakeTime()).request(PLAIN, "GET", URL)


# D-036: a suspension without Retry-After fails a check at once, waits in the background.
@respx.mock
async def test_suspension_fails_check_at_once(client: httpx.AsyncClient) -> None:
    respx.get(URL).respond(429)
    ft = FakeTime()
    with pytest.raises(SourceError, match="suspended for 30 s"):
        await make(client, ft, Mode.CHECK).request(TRON_LIKE, "GET", URL)
    assert ft.sleeps == []


@respx.mock
async def test_suspension_waited_in_background(client: httpx.AsyncClient) -> None:
    respx.get(URL).mock(side_effect=[httpx.Response(403), httpx.Response(200)])
    ft = FakeTime()
    r = await make(client, ft, Mode.BACKGROUND).request(TRON_LIKE, "GET", URL)
    assert r.status_code == 200
    assert ft.sleeps == [30.0]


@respx.mock
async def test_suspension_longer_than_pacer_limit_is_error(client: httpx.AsyncClient) -> None:
    respx.get(URL).respond(429)
    p = Provider("tron", refusal_wait=lambda r: 66.0)
    with pytest.raises(SourceError, match="would wait 66 s"):
        await make(client, FakeTime(), Mode.BACKGROUND).request(p, "GET", URL)


@respx.mock
async def test_refusal_without_wait_is_error(client: httpx.AsyncClient) -> None:
    respx.get(URL).respond(429)
    with pytest.raises(SourceError, match="no wait given"):
        await make(client, FakeTime(), Mode.BACKGROUND).request(PLAIN, "GET", URL)


@respx.mock
async def test_403_is_an_error_unless_provider_says_refusal(client: httpx.AsyncClient) -> None:
    respx.get(URL).respond(403, text="forbidden")
    with pytest.raises(SourceError, match="HTTP 403: forbidden"):
        await make(client, FakeTime()).request(PLAIN, "GET", URL)


@respx.mock
async def test_5xx_retried_then_ok(client: httpx.AsyncClient) -> None:
    respx.get(URL).mock(side_effect=[httpx.Response(502), httpx.Response(503), httpx.Response(200)])
    ft = FakeTime()
    assert (await make(client, ft).request(PLAIN, "GET", URL)).status_code == 200
    assert ft.sleeps == [0.5, 1.0]


@respx.mock
async def test_5xx_after_retries_is_error(client: httpx.AsyncClient) -> None:
    respx.get(URL).respond(503)
    with pytest.raises(SourceError, match="HTTP 503 after 3 tries"):
        await make(client, FakeTime()).request(PLAIN, "GET", URL)


@respx.mock
async def test_timeout_retried_then_error(client: httpx.AsyncClient) -> None:
    respx.get(URL).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(SourceError, match="timed out after 3 tries"):
        await make(client, FakeTime()).request(PLAIN, "GET", URL)


@respx.mock
async def test_connect_error_is_unreachable_at_once(client: httpx.AsyncClient) -> None:
    route = respx.get(URL).mock(side_effect=httpx.ConnectError("dns"))
    with pytest.raises(Unreachable):
        await make(client, FakeTime()).request(PLAIN, "GET", URL)
    assert route.call_count == 1


@respx.mock
async def test_error_message_never_contains_request_headers(client: httpx.AsyncClient) -> None:
    respx.get(URL).respond(401, text="bad token")
    with pytest.raises(SourceError) as exc:
        await make(client, FakeTime()).request(
            PLAIN, "GET", URL, headers={"Authorization": "Bearer secret-123"}
        )
    assert "secret-123" not in str(exc.value)


class _Stalls(httpx.AsyncByteStream):
    """Yields `data`, then stalls (ReadTimeout) unless `complete`."""

    def __init__(self, data: bytes, complete: bool) -> None:
        self.data, self.complete = data, complete

    async def __aiter__(self):  # type: ignore[no-untyped-def]
        yield self.data
        if not self.complete:
            raise httpx.ReadTimeout("stalled")


FILE = bytes(range(256)) * 40  # 10,240 bytes
LM = "Fri, 02 Oct 2026 15:55:45 GMT"


def full(n: int, complete: bool) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"content-length": str(len(FILE)), "last-modified": LM},
        stream=_Stalls(FILE[:n], complete),
    )


def part(
    start: int, n: int, complete: bool, lm: str = LM, total: int = len(FILE)
) -> httpx.Response:
    end = start + n - 1
    return httpx.Response(
        206,
        headers={"content-range": f"bytes {start}-{end}/{total}", "last-modified": lm},
        stream=_Stalls(FILE[start : start + n], complete),
    )


@respx.mock
async def test_download_resumes_after_stalls(client: httpx.AsyncClient) -> None:
    from amlcheck.net.http import download

    route = respx.get(URL).mock(
        side_effect=[full(3000, False), part(3000, 4000, False), part(7000, 3240, True)]
    )
    data = await download(make(client, FakeTime(), Mode.BACKGROUND), PLAIN, URL)
    assert data == FILE
    assert [c.request.headers.get("Range") for c in route.calls] == [
        None,
        "bytes=3000-",
        "bytes=7000-",
    ]


@pytest.mark.parametrize(
    "second",
    [
        full(len(FILE), True),  # server ignores Range: start over with the whole file
        part(3000, 7240, True, lm="Sat, 03 Oct 2026 00:00:00 GMT"),  # the file changed
    ],
)
@respx.mock
async def test_download_restarts_when_resume_is_not_safe(
    client: httpx.AsyncClient, second: httpx.Response
) -> None:
    from amlcheck.net.http import download

    respx.get(URL).mock(side_effect=[full(3000, False), second, full(len(FILE), True)])
    assert await download(make(client, FakeTime(), Mode.BACKGROUND), PLAIN, URL) == FILE


@respx.mock
async def test_download_gives_up_after_stalls_without_progress(client: httpx.AsyncClient) -> None:
    from amlcheck.net.http import download

    respx.get(URL).mock(side_effect=[full(3000, False)] + [part(3000, 0, False)] * 5)
    with pytest.raises(SourceError, match=r"stalled 5 times in a row.*3000 of 10240 bytes"):
        await download(make(client, FakeTime(), Mode.BACKGROUND), PLAIN, URL)


@respx.mock
async def test_download_client_error_is_immediate(client: httpx.AsyncClient) -> None:
    from amlcheck.net.http import download

    route = respx.get(URL).respond(404, text="gone")
    with pytest.raises(SourceError, match="HTTP 404: gone"):
        await download(make(client, FakeTime(), Mode.BACKGROUND), PLAIN, URL)
    assert route.call_count == 1
