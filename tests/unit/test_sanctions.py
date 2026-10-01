import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from amlcheck.config import Freshness, Network, Ofac
from amlcheck.core.address import detect
from amlcheck.core.clock import fixed
from amlcheck.core.models import Severity, SourceStatus
from amlcheck.net.http import Http, Mode, SourceError
from amlcheck.screening.sanctions import (
    SanctionsSource,
    normalise_listed,
    parse_sdn,
    store,
    sync,
)
from amlcheck.storage.db import open_db
from tests.unit.fakes import FakeTime

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "ofac" / "sdn_sample.xml"
NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / "a.db")


@pytest.fixture
def data() -> bytes:
    return FIX.read_bytes()


def loaded(conn: sqlite3.Connection, data: bytes, at: datetime = NOW) -> None:
    assert store(conn, data, parse_sdn(data), Ofac(), at).accepted


def source(conn: sqlite3.Connection, now: datetime = NOW) -> SanctionsSource:
    return SanctionsSource(conn, Freshness(), clock=fixed(now))


def test_parse_real_sample(data: bytes) -> None:
    p = parse_sdn(data)
    assert p.published_at == "2026-09-30"
    assert p.entry_count == 6
    labels = {a.currency_label for a in p.addresses}
    assert {"USDT", "TRX", "ETH", "XBT", "BSC", "ARB", "BNB"} <= labels
    assert all(a.checksum_ok for a in p.addresses)
    mesri = next(a for a in p.addresses if a.entry_id == "24003")
    assert mesri.entity_name == "Behzad MESRI"
    assert mesri.program == "CYBER2, HRIT-IR"
    evm = [a.address_norm for a in p.addresses if a.address_norm.startswith("0x")]
    assert all(x == x.lower() for x in evm)


def test_normalise_listed() -> None:
    assert normalise_listed(" 0x1CAb8177ACe78b1B6B1c393371F4f2dCAE40CbEB ") == (
        "0x1cab8177ace78b1b6b1c393371f4f2dcae40cbeb",
        True,
    )
    assert normalise_listed("0x1CAb8177ACe78b1B6B1c393371F4f2dCAE40CbEb")[1] is False  # typo
    assert normalise_listed("TNHrhtVnRMRaTJFRsqLUqSytCKsavXJJab")[1] is False
    assert normalise_listed("bnb136ns6lfw4zs5hg4n85vdthaad7hq5m4gtkgf23") == (
        "bnb136ns6lfw4zs5hg4n85vdthaad7hq5m4gtkgf23",
        True,
    )


def test_bad_checksum_entry_kept_and_flagged() -> None:
    xml = (
        b'<sdnList xmlns="x"><sdnEntry><uid>1</uid><lastName>T</lastName><idList><id>'
        b"<idType>Digital Currency Address - TRX</idType>"
        b"<idNumber>TNHrhtVnRMRaTJFRsqLUqSytCKsavXJJab</idNumber></id></idList></sdnEntry></sdnList>"
    )
    (a,) = parse_sdn(xml).addresses
    assert a.checksum_ok is False


def test_broken_or_empty_list_is_an_error() -> None:
    with pytest.raises(SourceError, match="not valid XML"):
        parse_sdn(b"<sdnList><sdnEntry>")
    with pytest.raises(SourceError, match="no entries"):
        parse_sdn(b'<sdnList xmlns="x"></sdnList>')


# AT-10: an address listed under another currency label → BLOCK, R-SAN-01 with entry ID and name.
@pytest.mark.parametrize(
    ("address", "entry"),
    [
        ("TUCsTq7TofTCJRRoHk6RvhMoS2mJLm5Yzq", "45404"),  # TRON address filed under XBT
        ("0x4F47BC496083c727c5fbe3ce9cdf2b0f6496270c".lower(), "42498"),  # ARB/BSC/ETH labels
        ("0x1CAb8177ACe78b1B6B1c393371F4f2dCAE40CbEB", "24003"),  # ETH label, checked on BSC
    ],
)
async def test_at10_listed_under_any_label_blocks(
    conn: sqlite3.Connection, data: bytes, address: str, entry: str
) -> None:
    loaded(conn, data)
    r = await source(conn).check(detect(address))
    assert r.status is SourceStatus.OK
    (f,) = r.findings
    assert f.rule_id == "R-SAN-01"
    assert f.severity is Severity.BLOCK
    (e,) = f.evidence["entries"]
    assert e["entry_id"] == entry
    assert e["entity_name"]
    assert f.evidence["snapshot"]["published"] == "2026-09-30"


async def test_one_finding_with_every_label(conn: sqlite3.Connection, data: bytes) -> None:
    loaded(conn, data)
    r = await source(conn).check(detect("0x4f47bc496083c727c5fbe3ce9cdf2b0f6496270c"))
    assert r.findings[0].evidence["entries"][0]["currency_labels"] == ["ARB", "BSC", "ETH"]


async def test_not_listed(conn: sqlite3.Connection, data: bytes) -> None:
    loaded(conn, data)
    r = await source(conn).check(detect("TNHrhtVnRMRaTJFRsqLUqSytCKsavXJJaa"))
    assert r.findings == ()
    assert r.status is SourceStatus.OK
    assert "list of 2026-09-30, downloaded 0 h ago" in (r.detail or "")


# AT-15: snapshot downloaded 49 h ago → stale (INCOMPLETE) unless a BLOCK finding exists.
async def test_at15_stale_after_48h_but_listing_still_reported(
    conn: sqlite3.Connection, data: bytes
) -> None:
    loaded(conn, data, NOW - timedelta(hours=49))
    clean = await source(conn).check(detect("TNHrhtVnRMRaTJFRsqLUqSytCKsavXJJaa"))
    assert clean.status is SourceStatus.STALE
    assert "run `amlcheck sync`" in (clean.detail or "")
    listed = await source(conn).check(detect("TUCsTq7TofTCJRRoHk6RvhMoS2mJLm5Yzq"))
    assert listed.status is SourceStatus.STALE
    assert listed.findings[0].severity is Severity.BLOCK
    fresh = await source(conn, NOW - timedelta(hours=1)).check(
        detect("TNHrhtVnRMRaTJFRsqLUqSytCKsavXJJaa")
    )
    assert fresh.status is SourceStatus.OK


async def test_never_downloaded_is_stale(conn: sqlite3.Connection) -> None:
    r = await source(conn).check(detect("TNHrhtVnRMRaTJFRsqLUqSytCKsavXJJaa"))
    assert r.status is SourceStatus.STALE
    assert "never downloaded" in (r.detail or "")
    h = await source(conn).health()
    assert h.status is SourceStatus.STALE


# AT-21: a new snapshot with 30% fewer addresses → rejected, previous kept, warning logged.
def test_at21_shrunk_list_rejected(
    conn: sqlite3.Connection, data: bytes, caplog: pytest.LogCaptureFixture
) -> None:
    loaded(conn, data)
    full = parse_sdn(data)
    keep = int(len(full.addresses) * 0.7)
    shrunk = full.__class__(full.published_at, full.entry_count, full.addresses[:keep])
    with caplog.at_level("WARNING", logger="amlcheck.sanctions"):
        r = store(conn, b"other", shrunk, Ofac(), NOW + timedelta(days=1))
    assert not r.accepted
    assert "rejected" in (r.reason or "")
    assert "OFAC snapshot rejected" in caplog.text
    assert conn.execute("SELECT count(*) FROM list_snapshots").fetchone()[0] == 1
    ok = full.__class__(
        full.published_at, full.entry_count, full.addresses[: int(len(full.addresses) * 0.81)]
    )
    assert store(conn, b"ok", ok, Ofac(), NOW + timedelta(days=2)).accepted


def test_old_snapshots_keep_rows_but_not_addresses(conn: sqlite3.Connection, data: bytes) -> None:
    for d in range(5):
        loaded(conn, data, NOW + timedelta(days=d))
    assert conn.execute("SELECT count(*) FROM list_snapshots").fetchone()[0] == 5
    kept = conn.execute("SELECT count(DISTINCT snapshot_id) FROM sanctioned_addresses").fetchone()
    assert kept[0] == 3


@respx.mock
async def test_sync_follows_redirect(conn: sqlite3.Connection, data: bytes) -> None:
    respx.get(Ofac().sdn_url).respond(302, headers={"Location": "https://s3.test/SDN.XML?sig=1"})
    respx.get("https://s3.test/SDN.XML").respond(200, content=data)
    async with httpx.AsyncClient() as c:
        http = Http(c, Network(), mode=Mode.BACKGROUND, sleep=FakeTime().sleep)
        r = await sync(http, conn, Ofac(), fixed(NOW))
    assert r.accepted
    assert r.address_count == len(parse_sdn(data).addresses)
    assert r.previous_count is None
