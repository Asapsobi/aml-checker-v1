"""Shared async HTTP client, timeouts and the refusal policy (methodology §2.4).

- `Retry-After` ≤ `[network] max_retry_after_seconds` (10 s) is waited out once; longer → error
  (D-011).
- A refusal without `Retry-After` asks the provider how long to wait:
  - budget pacing (HyperSync's window reset) may wait ≤ `max_pacer_wait_seconds` (65 s) in any mode
    (D-011);
  - a suspension (TronGrid's key, 30 s) fails a `CHECK` at once and is waited out only in
    `BACKGROUND` mode, within the same 65 s (D-036).
- Timeouts and 5xx are retried twice with a short backoff, then the source is in error.

Errors are `SourceError` with a plain reason. Request headers (keys) never appear in messages.
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

import httpx

from amlcheck.config import Network

TRANSIENT_RETRIES = 2
PACING_REFUSALS = 2
_BACKOFF_S = (0.5, 1.0)

type Sleep = Callable[[float], Awaitable[None]]


class Mode(StrEnum):
    CHECK = "check"  # an operator is waiting: no long waits
    BACKGROUND = "background"  # traces, syncs: may wait out a suspension within the pacer limit


class SourceError(Exception):
    """A provider could not answer. Becomes a `failed()` source result, never a crash."""

    def __init__(self, source: str, reason: str) -> None:
        super().__init__(f"{source}: {reason}")
        self.source = source
        self.reason = reason


class Unreachable(SourceError):
    """Could not connect at all (DNS, refused connection). Callers may try a fallback host."""


class LimiterError(Exception):
    """A limiter would have to wait longer than allowed."""


class Limiter(Protocol):
    async def acquire(self) -> None: ...

    def observe(self, status: int, headers: httpx.Headers) -> None: ...


def _no_wait(_: httpx.Response) -> float | None:
    return None


@dataclass(frozen=True)
class Provider:
    name: str
    limiter: Limiter | None = None
    refusal_statuses: frozenset[int] = frozenset({429})
    #: Seconds to wait on a refusal without `Retry-After`; None = don't wait (error).
    refusal_wait: Callable[[httpx.Response], float | None] = field(default=_no_wait)
    #: True: the wait is budget pacing (any mode). False: a suspension (BACKGROUND only).
    refusal_is_pacing: bool = False


class Http:
    def __init__(
        self,
        client: httpx.AsyncClient,
        network: Network,
        *,
        mode: Mode,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._client = client
        self._network = network
        self._mode = mode
        self._sleep = sleep
        #: Requests sent per provider, every attempt counted (trace budgets, PRD G8).
        self.sent: Counter[str] = Counter()

    @property
    def mode(self) -> Mode:
        return self._mode

    async def request(
        self,
        provider: Provider,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, str | int] | None = None,
        json: Any = None,
        follow_redirects: bool = False,
    ) -> httpx.Response:
        transient = 0
        refusals = 0
        while True:
            if provider.limiter is not None:
                try:
                    await provider.limiter.acquire()
                except LimiterError as e:
                    raise SourceError(provider.name, str(e)) from None
            self.sent[provider.name] += 1
            try:
                resp = await self._client.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    json=json,
                    timeout=self._network.timeout_seconds,
                    follow_redirects=follow_redirects,
                )
            except httpx.ConnectError as e:
                raise Unreachable(provider.name, f"cannot connect ({type(e).__name__})") from None
            except (httpx.TimeoutException, httpx.TransportError) as e:
                transient += 1
                if transient > TRANSIENT_RETRIES:
                    reason = (
                        "timed out" if isinstance(e, httpx.TimeoutException) else "network error"
                    )
                    raise SourceError(provider.name, f"{reason} after {transient} tries") from None
                await self._sleep(_BACKOFF_S[transient - 1])
                continue
            if provider.limiter is not None:
                provider.limiter.observe(resp.status_code, resp.headers)
            if resp.status_code in provider.refusal_statuses:
                refusals += 1
                await self._sleep(self._refusal_wait(provider, resp, refusals))
                continue
            if resp.status_code >= 500:
                transient += 1
                if transient > TRANSIENT_RETRIES:
                    raise SourceError(
                        provider.name, f"HTTP {resp.status_code} after {transient} tries"
                    )
                await self._sleep(_BACKOFF_S[transient - 1])
                continue
            if resp.status_code >= 400:
                raise SourceError(provider.name, f"HTTP {resp.status_code}: {_snippet(resp)}")
            return resp

    def _refusal_wait(self, provider: Provider, resp: httpx.Response, refusals: int) -> float:
        name, net = provider.name, self._network
        retry_after = resp.headers.get("Retry-After")
        if retry_after is not None:
            secs = _seconds(retry_after)
            if secs is None or secs > net.max_retry_after_seconds:
                raise SourceError(name, f"rate limited; asked to wait {retry_after.strip()} s")
            if refusals > 1:
                raise SourceError(name, "rate limited again after waiting")
            return secs
        wait = provider.refusal_wait(resp)
        if wait is None:
            raise SourceError(name, f"rate limited (HTTP {resp.status_code}, no wait given)")
        if not provider.refusal_is_pacing and self._mode is Mode.CHECK:
            raise SourceError(name, f"rate limited; suspended for {wait:g} s")
        if wait > net.max_pacer_wait_seconds:
            raise SourceError(name, f"rate limited; would wait {wait:g} s")
        if refusals > PACING_REFUSALS:
            raise SourceError(name, f"rate limited {refusals} times in a row")
        return wait


def _seconds(value: str) -> float | None:
    """`Retry-After` in seconds. HTTP-date forms are not waited on (returns None)."""
    try:
        secs = float(value.strip())
    except ValueError:
        return None
    return secs if secs >= 0 else None


def _snippet(resp: httpx.Response) -> str:
    text = " ".join(resp.text.split())
    return text[:200] if text else "(empty body)"


_CONTENT_RANGE = re.compile(r"^bytes (\d+)-(\d+)/(\d+)$")
DOWNLOAD_STALLS = 5


async def download(http: Http, provider: Provider, url: str) -> bytes:
    """GET a large file, resuming after stalls instead of starting over (D-040).

    Each attempt streams; a stall longer than `[network] timeout_seconds` ends it, and the next one
    asks for the rest with `Range`. If the server's copy changed (size or `Last-Modified`), or it
    ignores `Range`, the download restarts from zero. Gives up after `DOWNLOAD_STALLS` attempts in a
    row that brought no new bytes. Redirects are followed and never logged (signed URLs).
    """
    buf = bytearray()
    total: int | None = None
    version: str | None = None
    stalls = 0
    while True:
        before = len(buf)
        headers = {"Range": f"bytes={len(buf)}-"} if buf else {}
        try:
            async with http._client.stream(
                "GET",
                url,
                headers=headers,
                follow_redirects=True,
                timeout=http._network.timeout_seconds,
            ) as resp:
                if resp.status_code == 206 and buf:
                    m = _CONTENT_RANGE.match(resp.headers.get("content-range", ""))
                    same = (
                        m is not None
                        and int(m.group(1)) == len(buf)
                        and int(m.group(3)) == total
                        and resp.headers.get("last-modified") == version
                    )
                    if not same:
                        buf.clear()
                        continue
                elif resp.status_code == 200:
                    buf.clear()
                    length = resp.headers.get("content-length")
                    total = int(length) if length and length.isdigit() else None
                    version = resp.headers.get("last-modified")
                elif resp.status_code >= 500 or resp.status_code in provider.refusal_statuses:
                    raise httpx.TransportError(f"HTTP {resp.status_code}")
                else:
                    await resp.aread()
                    raise SourceError(provider.name, f"HTTP {resp.status_code}: {_snippet(resp)}")
                async for chunk in resp.aiter_bytes():
                    buf.extend(chunk)
            if total is None or len(buf) >= total:
                return bytes(buf)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            stalls = 0 if len(buf) > before else stalls + 1
            if stalls >= DOWNLOAD_STALLS:
                got = f"{len(buf)} of {total} bytes" if total else f"{len(buf)} bytes"
                raise SourceError(
                    provider.name,
                    f"download stalled {stalls} times in a row ({type(e).__name__}); got {got}",
                ) from None
            await http._sleep(_BACKOFF_S[min(stalls, len(_BACKOFF_S)) - 1] if stalls else 0)
