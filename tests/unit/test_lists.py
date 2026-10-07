"""Sanctions lists beside OFAC: UK, EU, NBCTF (methodology §13.1, D-084, D-085, AT-69)."""

import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from amlcheck.config import Freshness
from amlcheck.core.address import detect
from amlcheck.core.clock import fixed, to_db
from amlcheck.core.models import Chain, Severity, SourceStatus
from amlcheck.intel.names import sanctions_entry
from amlcheck.screening import lists
from amlcheck.screening.exposure import local_flags
from amlcheck.screening.sanctions import SanctionsSource, store
from amlcheck.storage.db import open_db

FIX = Path(__file__).parent.parent / "fixtures" / "lists"
NOW = datetime(2026, 10, 5, 12, tzinfo=UTC)
XINBI = "TNwe3WoEX6XrQ5NPAeWPkvpTtdaf27XYxK"  # UK GHR0190 (Xinbi); also OFAC-listed in reality
GRINEX = "TC8axQvzJEVR3NKN6mZnJtGy7537GEmh38"  # EU.13770.38


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / "a.db")


def uk() -> bytes:
    return (FIX / "uk_sample.xml").read_bytes()


def eu() -> bytes:
    return (FIX / "eu_sample.xml").read_bytes()


def source(conn: sqlite3.Connection, spec: lists.ListSpec, now: datetime = NOW) -> SanctionsSource:
    return SanctionsSource(
        conn,
        Freshness(),
        clock=fixed(now),
        source=spec.source,
        label=spec.label,
        list_name=spec.list_name,
        required=spec.required,
    )


def test_parse_uk() -> None:
    parsed = lists.parse_uk(uk())
    assert (parsed.published_at, parsed.entry_count) == ("2026-10-02", 3)
    by = {a.address_norm: a for a in parsed.addresses}
    assert by[XINBI].entity_name == "XINBI COMPANY LIMITED"
    assert by[XINBI].entry_id == "GHR0190"
    assert by[XINBI].program == "The Global Human Rights Sanctions Regulations 2020"
    assert {a.entry_id for a in parsed.addresses} == {"GHR0190", "RUS3602"}  # AFG0001 has none
    assert all(a.checksum_ok for a in parsed.addresses)


def test_parse_eu() -> None:
    parsed = lists.parse_eu(eu())
    assert (parsed.published_at, parsed.entry_count) == ("2026-09-22", 3)
    by = {a.address_norm: a for a in parsed.addresses}
    assert (by[GRINEX].entity_name, by[GRINEX].entry_id) == ("Grinex", "EU.13770.38")
    assert by[GRINEX].program == "UKR"
    assert "0x3051ca7cb7f6c599fa2f27385ad75010cf0f2bbf" in by  # BSC, lowercased (F3.2)


def test_addresses_in_free_text_keep_only_valid_checksums() -> None:
    bad = XINBI[:-1] + ("L" if XINBI[-1] != "L" else "M")  # one character off
    text = f"wallets: {XINBI}; {bad}; {XINBI}, ETH 0xAbCdEf0123456789aBcDeF0123456789AbCdEf01."
    assert lists.addresses_in(text) == [XINBI, "0xabcdef0123456789abcdef0123456789abcdef01"]


# AT-69 (lists): an address on the UK list BLOCKs, naming the list and the entry.
async def test_at69_uk_listing_blocks(conn: sqlite3.Connection) -> None:
    assert store(
        conn, uk(), lists.parse_uk(uk()), Decimal("0.8"), NOW, source=lists.UK.source
    ).accepted
    r = await source(conn, lists.UK).check(detect(XINBI))
    assert r.status is SourceStatus.OK
    (f,) = r.findings
    assert (f.rule_id, f.severity, f.source) == ("R-SAN-01", Severity.BLOCK, "uk_sanctions")
    assert f.summary.startswith(
        "Listed on the UK sanctions list: XINBI COMPANY LIMITED (entry GHR0190"
    )
    assert f.evidence["list"] == "UK sanctions"
    clean = await source(conn, lists.UK).check(detect("TNHrhtVnRMRaTJFRsqLUqSytCKsavXJJaa"))
    assert clean.findings == ()


async def test_freshness_and_nbctf_not_required(conn: sqlite3.Connection) -> None:
    store(conn, eu(), lists.parse_eu(eu()), Decimal("0.8"), NOW, source=lists.EU.source)
    later = NOW + timedelta(hours=49)
    stale = await source(conn, lists.EU, later).check(detect(GRINEX))
    assert stale.status is SourceStatus.STALE  # required, older than 48 h (D-084)
    assert stale.findings[0].severity is Severity.BLOCK  # a listing still blocks (AT-15)
    nbctf = await source(conn, lists.NBCTF).check(detect(GRINEX))
    assert (nbctf.status, nbctf.required) == (SourceStatus.SKIPPED, False)
    assert "import-nbctf" in (nbctf.detail or "")


def test_flags_and_names_across_lists(conn: sqlite3.Connection) -> None:
    store(conn, uk(), lists.parse_uk(uk()), Decimal("0.8"), NOW, source=lists.UK.source)
    store(conn, eu(), lists.parse_eu(eu()), Decimal("0.8"), NOW, source=lists.EU.source)
    flags = local_flags(conn, Chain.TRON, [XINBI, GRINEX, "TNHrhtVnRMRaTJFRsqLUqSytCKsavXJJaa"])
    assert flags[XINBI] == {"sanctioned"}
    assert flags[GRINEX] == {"sanctioned"}
    assert sanctions_entry(conn, GRINEX) == "EU sanctions: Grinex"
    assert sanctions_entry(conn, XINBI) == "UK sanctions: XINBI COMPANY LIMITED"
    conn.execute(
        "INSERT INTO list_snapshots (source, fetched_at, published_at, sha256, entry_count, "
        "address_count) VALUES ('ofac_sdn', ?, '2026-10-01', 'h', 1, 1)",
        (to_db(NOW),),
    )
    snap = conn.execute("SELECT max(id) FROM list_snapshots WHERE source = 'ofac_sdn'").fetchone()[
        0
    ]
    conn.execute(
        "INSERT INTO sanctioned_addresses VALUES (?, ?, 'XBT', '1', 'XINBI GUARANTEE', 'SDGT', 1)",
        (snap, XINBI),
    )
    assert sanctions_entry(conn, XINBI) == "OFAC SDN: XINBI GUARANTEE"  # OFAC first


def test_a_shrunken_list_is_rejected(conn: sqlite3.Connection) -> None:
    parsed = lists.parse_uk(uk())
    store(conn, uk(), parsed, Decimal("0.8"), NOW, source=lists.UK.source)
    half = type(parsed)(parsed.published_at, parsed.entry_count, parsed.addresses[:10])
    r = store(conn, b"x", half, Decimal("0.8"), NOW, source=lists.UK.source)
    assert not r.accepted
    assert "rejected" in (r.reason or "")
