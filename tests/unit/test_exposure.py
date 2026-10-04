import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from amlcheck.chain.base import Transfer
from amlcheck.chain.cache import TransferCache
from amlcheck.config import Cache, Exposure, Heuristics
from amlcheck.core.address import detect
from amlcheck.core.clock import fixed, to_db
from amlcheck.core.models import Chain, Severity, SourceStatus
from amlcheck.intel.labels_csv import parse, replace_all
from amlcheck.screening.exposure import ExposureSource
from amlcheck.storage.db import open_db
from tests.unit.test_cache import Clock, FakeSource

NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)
ME = "TNHrhtVnRMRaTJFRsqLUqSytCKsavXJJaa"


def tr(hours_ago: float, other: str, amount: str, *, out: bool = False, n: int = 0) -> Transfer:
    return Transfer(
        Chain.TRON,
        f"tx-{other}-{hours_ago}-{amount}-{out}-{n}",
        0,
        None,
        NOW - timedelta(hours=hours_ago),
        ME if out else other,
        other if out else ME,
        Decimal(amount),
    )


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = open_db(tmp_path / "a.db")
    c.execute(
        "INSERT INTO list_snapshots (id, source, fetched_at, published_at, sha256, entry_count, "
        "address_count) VALUES (1, 'ofac_sdn', ?, '2026-09-30', 'h', 1, 1)",
        (to_db(NOW),),
    )
    c.execute(
        "INSERT INTO sanctioned_addresses "
        "VALUES (1, 'TSANCTIONED', 'USDT', '42', 'Bad Co', 'SDGT', 1)"
    )
    for i, (event, block) in enumerate(
        [("AddedBlackList", 10), ("AddedBlackList", 11), ("RemovedBlackList", 12)]
    ):
        who = "TFROZEN" if i == 0 else "TRELEASED"
        c.execute(
            "INSERT INTO issuer_events VALUES ('tron', 'TR7N', ?, ?, NULL, ?, 0, ?, ?)",
            (who, event, f"ev{i}", block, to_db(NOW - timedelta(days=30))),
        )
    return c


def source(
    conn: sqlite3.Connection,
    transfers: list[Transfer],
    *,
    exposure: Exposure | None = None,
    **heu: object,
) -> ExposureSource:
    src = FakeSource(Clock(NOW), transfers, first=NOW - timedelta(days=400), chain=Chain.TRON)
    cache = TransferCache(conn, {Chain.TRON: src}, Cache(), clock=fixed(NOW))
    return ExposureSource(cache, conn, exposure or Exposure(), Heuristics(**heu), clock=fixed(NOW))  # type: ignore[arg-type]


async def rules(
    conn: sqlite3.Connection, transfers: list[Transfer], **heu: Any
) -> dict[str, list[dict[str, object]]]:
    conn.execute("DELETE FROM transfers")  # each call sees only its own history
    conn.execute("DELETE FROM history_windows")
    r = await source(conn, transfers, **heu).check(detect(ME))
    out: dict[str, list[dict[str, object]]] = {}
    for f in r.findings:
        out.setdefault(f.rule_id, []).append(
            {"summary": f.summary, "severity": f.severity, **f.evidence}
        )
    return out


# AT-22: target received 1,200 USDT from a frozen address → REVIEW, R-EXP-01 with tx evidence.
async def test_at22_frozen_counterparty(conn: sqlite3.Connection) -> None:
    found = await rules(conn, [tr(10, "TFROZEN", "1200"), tr(5, "TOK", "50000")])
    (f,) = found["R-EXP-01"]
    assert f["severity"] is Severity.REVIEW  # D-014
    cp = f["counterparty"]
    assert cp["address"] == "TFROZEN"  # type: ignore[index]
    assert cp["flags"] == ["frozen"]  # type: ignore[index]
    assert cp["received_usdt"] == "1200"  # type: ignore[index]
    assert cp["largest"][0]["tx_hash"].startswith("tx-TFROZEN")  # type: ignore[index]


async def test_released_and_sanctioned_counterparties(conn: sqlite3.Connection) -> None:
    found = await rules(conn, [tr(10, "TRELEASED", "500"), tr(9, "TSANCTIONED", "30", out=True)])
    (f,) = found["R-EXP-01"]
    assert f["counterparty"]["address"] == "TSANCTIONED"  # type: ignore[index]  # released ≠ frozen


# AT-23: 6% of inflow from flagged counterparties → R-EXP-02; 4% → not.
@pytest.mark.parametrize(("bad", "raised"), [("600", True), ("400", False)])
async def test_at23_flagged_inflow_share(conn: sqlite3.Connection, bad: str, raised: bool) -> None:
    good = str(10_000 - int(bad))
    found = await rules(conn, [tr(10, "TSANCTIONED", bad), tr(9, "TOK", good)])
    assert ("R-EXP-02" in found) is raised
    if raised:
        assert found["R-EXP-02"][0]["share"] == "0.06"


async def test_risky_label_counts_as_flagged_and_raises_heu05(conn: sqlite3.Connection) -> None:
    replace_all(
        conn,
        parse("address,chain,tag,note,source\nTNPeeaaFB7K9cmo4uQpcU32zGK8G1NYqeL,tron,mixer,,\n"),
    )
    found = await rules(
        conn, [tr(10, "TNPeeaaFB7K9cmo4uQpcU32zGK8G1NYqeL", "900"), tr(9, "TOK", "100")]
    )
    assert "R-EXP-02" in found  # 90% from a mixer
    assert "mixer" in str(found["R-HEU-05"][0]["summary"])


# AT-24: pass-through (95% out within 2 h) → R-HEU-02; counterparty on allowlist → not raised.
async def test_at24_pass_through_and_allowlist(conn: sqlite3.Connection) -> None:
    xs = [tr(20, "TIN", "1000"), tr(18, "TOUT", "950", out=True)]
    assert "R-HEU-02" in await rules(conn, xs)
    replace_all(
        conn,
        parse(
            "address,chain,tag,note,source\nTNPeeaaFB7K9cmo4uQpcU32zGK8G1NYqeL,tron,allowlist,,\n"
        ),
    )
    xs2 = [tr(20, "TNPeeaaFB7K9cmo4uQpcU32zGK8G1NYqeL", "1000"), tr(18, "TOUT", "950", out=True)]
    assert "R-HEU-02" not in await rules(conn, xs2)


async def test_allowlist_never_cancels_a_freeze_finding(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO labels VALUES ('tron', 'TFROZEN', 'allowlist', NULL, NULL)")
    found = await rules(conn, [tr(10, "TFROZEN", "1200")])
    assert "R-EXP-01" in found


# AT-25: 60 senders of 20 USDT within 3 h → R-HEU-03.
async def test_at25_fan_in(conn: sqlite3.Connection) -> None:
    xs = [tr(3 * i / 59, f"TS{i}", "20", n=i) for i in range(60)]
    found = await rules(conn, xs)
    assert found["R-HEU-03"][0]["senders"] == 60
    big = [tr(3 * i / 59, f"TS{i}", "100", n=i) for i in range(60)]  # not small: < 100 only
    assert "R-HEU-03" not in await rules(conn, big)


async def test_fan_out(conn: sqlite3.Connection) -> None:
    xs = [tr(30, "TFUND", "100000")] + [
        tr(5 - i / 20, f"TR{i}", "10", out=True, n=i) for i in range(51)
    ]
    found = await rules(conn, xs)
    assert found["R-HEU-04"][0]["recipients"] == 51
    assert "R-HEU-04" not in await rules(conn, xs[:-1])  # 50 is not more than 50


# AT-26: more than max_transfers in the window → exposure stale → INCOMPLETE.
async def test_at26_too_many_transfers_is_stale(conn: sqlite3.Connection) -> None:
    xs = [tr(i / 100, f"TC{i % 7}", "1", n=i) for i in range(5_001)]
    r = await source(conn, xs, exposure=Exposure(max_transfers=5000)).check(detect(ME))
    assert r.status is SourceStatus.STALE
    assert "more than 5000 transfers" in (r.detail or "")


@pytest.mark.parametrize(
    ("first_days_ago", "transfers", "raised"),
    [(400, True, False), (3, True, True), (None, False, True)],
)
async def test_heu01_new_or_unused(
    conn: sqlite3.Connection, first_days_ago: int | None, transfers: bool, raised: bool
) -> None:
    xs = [tr(10, "TX", "5")] if transfers else []
    src = FakeSource(
        Clock(NOW),
        xs,
        first=NOW - timedelta(days=first_days_ago) if first_days_ago else None,
        chain=Chain.TRON,
    )
    cache = TransferCache(conn, {Chain.TRON: src}, Cache(), clock=fixed(NOW))
    r = await ExposureSource(cache, conn, Exposure(), Heuristics(), clock=fixed(NOW)).check(
        detect(ME)
    )
    heu = [f for f in r.findings if f.rule_id == "R-HEU-01"]
    assert bool(heu) is raised
    if heu:
        assert heu[0].evidence["priority"] == "low"


async def test_exp01_capped_at_ten_largest_first(conn: sqlite3.Connection) -> None:
    for i in range(12):
        conn.execute(
            "INSERT INTO sanctioned_addresses VALUES (1, ?, 'USDT', ?, 'X', 'P', 1)",
            (f"TS{i:02d}", str(i)),
        )
    xs = [tr(10, f"TS{i:02d}", str(100 + i), n=i) for i in range(12)]
    found = await rules(conn, xs)
    got = [f["counterparty"]["address"] for f in found["R-EXP-01"]]  # type: ignore[index]
    assert got == [f"TS{i:02d}" for i in range(11, 1, -1)]


async def test_clean_history_evidence(conn: sqlite3.Connection) -> None:
    r = await source(conn, [tr(10, "TA", "100"), tr(5, "TB", "40", out=True)]).check(detect(ME))
    assert r.status is SourceStatus.OK
    assert r.findings == ()
    ev = r.evidence
    assert (ev["received_usdt"], ev["sent_usdt"], ev["counterparty_count"]) == ("100", "40", 2)
    assert [c["address"] for c in ev["counterparties"]] == ["TA", "TB"]
