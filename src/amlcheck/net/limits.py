"""Process-wide limiters, one per provider (architecture §5, PRD F2.5).

- `TokenBucket` spaces requests evenly at a fixed rate. TronGrid: 10/s against the key's 15/s, since
  going over suspends the key for 30 s (VS-05, D-036).
- `BudgetPacer` follows the provider's own budget headers (`x-ratelimit-remaining`, `-reset`,
  `-cost`) and waits for the next window instead of being refused, up to a limit (D-011, AT-06).
  HyperSync: one budget per token across all its hosts (VS-06).

The composition root (CLI, later web/API) creates one of each per process and passes it down.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

import httpx

from amlcheck.net.http import LimiterError

type Monotonic = Callable[[], float]
type Sleep = Callable[[float], Awaitable[None]]


class TokenBucket:
    """Evenly spaced slots at `rate` per second; concurrent callers queue for the next slot."""

    def __init__(
        self,
        rate: float,
        *,
        monotonic: Monotonic = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        self._interval = 1.0 / rate
        self._monotonic = monotonic
        self._sleep = sleep
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = self._monotonic()
            slot = max(now, self._next)
            self._next = slot + self._interval
        if slot > now:
            await self._sleep(slot - now)

    def observe(self, status: int, headers: httpx.Headers) -> None:
        pass


class BudgetPacer:
    """Waits for the provider's budget window when the remaining budget can't pay for a request."""

    def __init__(
        self,
        max_wait: float,
        *,
        monotonic: Monotonic = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._max_wait = max_wait
        self._monotonic = monotonic
        self._sleep = sleep
        self._remaining: int | None = None
        self._reset_at: float | None = None
        self._cost = 1
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        # The lock is held while waiting on purpose: nobody may spend from an empty window.
        async with self._lock:
            now = self._monotonic()
            if self._reset_at is not None and now >= self._reset_at:
                self._remaining, self._reset_at = None, None
            if self._remaining is not None and self._remaining < self._cost:
                wait = (self._reset_at - now) if self._reset_at is not None else self._max_wait + 1
                if wait > self._max_wait:
                    raise LimiterError(f"budget spent; next window in {wait:.0f} s")
                await self._sleep(wait)
                self._remaining, self._reset_at = None, None
            if self._remaining is not None:
                self._remaining -= self._cost

    def observe(self, status: int, headers: httpx.Headers) -> None:
        remaining = _int(headers.get("x-ratelimit-remaining"))
        reset = _int(headers.get("x-ratelimit-reset"))
        cost = _int(headers.get("x-ratelimit-cost"))
        # The cost header read 0 one day (data sources §7): never learn a zero cost.
        if cost is not None and cost > 0:
            self._cost = cost
        if remaining is not None:
            self._remaining = remaining
        if reset is not None:
            self._reset_at = self._monotonic() + reset


def _int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None
