"""T-3.07: a 1,000-transfer address checks in well under 60 s (p95), offline."""

import statistics
import tempfile
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from amlcheck.chain.base import Transfer
from amlcheck.chain.cache import TransferCache
from amlcheck.config import Cache, Exposure, Heuristics, Settings
from amlcheck.core.address import detect
from amlcheck.core.clock import fixed
from amlcheck.core.engine import screen
from amlcheck.core.models import Chain
from amlcheck.screening.exposure import ExposureSource
from amlcheck.storage.db import open_db
from tests.unit.test_cache import Clock, FakeSource

NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)
ME = "TNHrhtVnRMRaTJFRsqLUqSytCKsavXJJaa"


def history(n: int) -> list[Transfer]:
    out = []
    for i in range(n):
        incoming = i % 3 != 0
        other = f"TC{i % 250:03d}"
        out.append(
            Transfer(
                Chain.TRON,
                f"tx{i}",
                0,
                None,
                NOW - timedelta(minutes=170 * i),
                other if incoming else ME,
                ME if incoming else other,
                Decimal(10 + i % 997),
            )
        )
    return out


async def test_1000_transfers_p95_under_60s() -> None:
    timings = []
    xs = history(1_000)
    for _ in range(20):
        with tempfile.TemporaryDirectory() as d:
            conn = open_db(Path(d) / "a.db")
            try:
                src = FakeSource(Clock(NOW), xs, first=NOW - timedelta(days=500), chain=Chain.TRON)
                cache = TransferCache(conn, {Chain.TRON: src}, Cache(), clock=fixed(NOW))
                exposure = ExposureSource(cache, conn, Exposure(), Heuristics(), clock=fixed(NOW))
                t0 = time.perf_counter()
                r = await screen(
                    detect(ME), [exposure], conn=conn, settings=Settings(), now=fixed(NOW)
                )
                timings.append(time.perf_counter() - t0)
            finally:
                conn.close()
    assert r.sources[0].evidence["transfers"] == 1_000
    p95 = statistics.quantiles(timings, n=20)[18]
    median = statistics.median(timings)
    print(f"1,000-transfer check, offline: p95 {p95 * 1000:.0f} ms, median {median * 1000:.0f} ms")
    assert p95 < 60
