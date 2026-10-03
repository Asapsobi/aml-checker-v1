import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from amlcheck.config import Settings
from amlcheck.core.address import detect
from amlcheck.core.clock import fixed
from amlcheck.core.engine import screen
from amlcheck.core.models import Chain, Severity, SourceStatus
from amlcheck.intel import registry
from amlcheck.intel.labels_csv import parse, replace_all
from amlcheck.intel.lookalike import LookalikeSource, marked
from amlcheck.intel.packs import import_pack
from amlcheck.intel.registry import lookalike_key
from amlcheck.intel.store import IntelError, IntelStore, NewLabel
from amlcheck.storage.db import open_db
from tests.unit.test_engine import Fake

NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)
# EVM look-alikes: same first 4 / last 4 of the body, different middle. Valid TRON look-alikes need
# GPU grinding to satisfy base58check, so these tests use 0x addresses, which any hex makes valid.
REAL = "0x8894e0a0c962cb723c1976a4421c95949be2d4e3"
FAKE = "0x8894" + "1" * 32 + "d4e3"
OTHER = "0x" + "ef" * 20

PACK = (
    "address,chain,category,note\n"
    "TNHrhtVnRMRaTJFRsqLUqSytCKsavXJJaa,tron,scam,reported\n"
    "0x55d398326f99059ff775485246999027b3197955,bsc,exchange_regulated,\n"
)


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / "a.db")


# --- label packs ----------------------------------------------------------------------------------


# AT-30: `intel import-pack` without --licence → refused, nothing imported.
@pytest.mark.parametrize("licence", [None, "", "  "])
def test_at30_pack_without_licence_refused(conn: sqlite3.Connection, licence: str | None) -> None:
    store = IntelStore(conn, clock=fixed(NOW))
    with pytest.raises(IntelError, match="--licence"):
        import_pack(store, conn, PACK, name="acme", licence=licence, by="sobhan")
    assert store.labels() == []


def test_pack_import_and_reimport(conn: sqlite3.Connection) -> None:
    store = IntelStore(conn, clock=fixed(NOW))
    r = import_pack(store, conn, PACK, name="acme-2026", licence="ACME EULA §3, internal", by="s")
    assert (r.added, r.retracted) == (2, 0)
    rows = store.labels()
    assert {x.provenance for x in rows} == {"import"}
    assert {x.licence for x in rows} == {"ACME EULA §3, internal"}
    assert {x.source_ref for x in rows} == {"pack:acme-2026"}
    smaller = "address,chain,category,note\nTNHrhtVnRMRaTJFRsqLUqSytCKsavXJJaa,tron,scam,\n"
    r2 = import_pack(store, conn, smaller, name="acme-2026", licence="ACME EULA §3", by="s")
    assert (r2.added, r2.retracted) == (1, 2)
    assert len(store.labels()) == 1
    assert len(store.labels(include_retracted=True)) == 3


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("address,chain,category,note\nnonsense,tron,scam,\n", "line 2"),
        (
            "address,chain,category,note\nTNHrhtVnRMRaTJFRsqLUqSytCKsavXJJaa,tron,sanctioned,\n",
            "only come from",
        ),
        ("addr,cat\nx,y\n", "pack header"),
    ],
)
def test_bad_pack_imports_nothing(conn: sqlite3.Connection, text: str, message: str) -> None:
    store = IntelStore(conn, clock=fixed(NOW))
    with pytest.raises(IntelError, match=message):
        import_pack(store, conn, text, name="p", licence="L", by=None)
    assert store.labels() == []


# --- registry -------------------------------------------------------------------------------------


# AT-28: registry after 10 checks over 4 addresses, then `cp rebuild` → identical rows.
async def test_at28_rebuild_gives_identical_rows(conn: sqlite3.Connection) -> None:
    targets = [
        "TNHrhtVnRMRaTJFRsqLUqSytCKsavXJJaa",
        "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t",
        REAL,
        OTHER,
    ]
    plan = [0, 1, 2, 3, 0, 0, 1, 2, 2, 3]
    clients = ["acme", None, "Beta", "acme", "beta", None, "acme", None, "acme", "zeta"]
    for i, (t, c) in enumerate(zip(plan, clients, strict=True)):
        rules = ("R-SAN-01",) if i == 5 else ()
        await screen(
            detect(targets[t]),
            [Fake("ofac_sdn", rules=rules)],
            conn=conn,
            settings=Settings(),
            client=c,
            now=fixed(NOW + timedelta(minutes=i)),
        )
    live = registry.snapshot(conn)
    assert len(live) == 4
    cp = registry.get(conn, Chain.TRON, targets[0])
    assert cp is not None
    assert (cp.check_count, cp.last_verdict, cp.clients) == (3, "BLOCK", ("acme", "beta"))
    assert registry.rebuild(conn) == 10
    assert registry.snapshot(conn) == live


def test_lookalike_key() -> None:
    assert lookalike_key(Chain.TRON, "TNHrhtVnRMRaTJFRsqLUqSytCKsavXJJaa") == "NHrhJJaa"
    assert lookalike_key(Chain.BSC, REAL) == lookalike_key(Chain.BSC, FAKE) == "8894d4e3"
    assert lookalike_key(Chain.BSC, "0xABCD" + "0" * 32 + "1234") == "abcd1234"


# --- look-alike guard -----------------------------------------------------------------------------


# AT-29: target shares first 4 and last 4 body characters with a registry address → REVIEW,
# R-HEU-06 naming both full addresses.
async def test_at29_lookalike_of_a_counterparty(conn: sqlite3.Connection) -> None:
    await screen(
        detect(REAL),
        [Fake("ofac_sdn")],
        conn=conn,
        settings=Settings(),
        client="acme",
        now=fixed(NOW),
    )
    r = await LookalikeSource(conn, clock=fixed(NOW)).check(detect(FAKE))
    assert r.status is SourceStatus.OK
    (f,) = r.findings
    assert (f.rule_id, f.severity) == ("R-HEU-06", Severity.REVIEW)
    assert f.evidence["looks_like"]["address"] == REAL
    assert f.evidence["target"] == FAKE
    assert f.evidence["looks_like"]["clients"] == ["acme"]
    assert "0x8894[" in f.summary
    assert "]d4e3" in f.summary


async def test_lookalike_of_a_trusted_label_and_not_of_itself(conn: sqlite3.Connection) -> None:
    replace_all(conn, parse(f"address,chain,tag,note,source\n{REAL},bsc,allowlist,,\n"))
    r = await LookalikeSource(conn, clock=fixed(NOW)).check(detect(FAKE))
    assert r.findings[0].evidence["looks_like"]["why_known"] == "trusted"
    IntelStore(conn, clock=fixed(NOW)).add_label(
        NewLabel(Chain.BSC, FAKE, "own_or_trusted", "operator", "case:2")
    )
    same = await LookalikeSource(conn, clock=fixed(NOW)).check(detect(REAL))
    assert [f.evidence["looks_like"]["address"] for f in same.findings] == [FAKE]
    alone = await LookalikeSource(conn, clock=fixed(NOW)).check(detect(OTHER))
    assert alone.findings == ()


def test_marked() -> None:
    assert marked(REAL, FAKE) == "0x8894[e0a0c962cb723c1976a4421c95949be2]d4e3"
    assert marked(FAKE, REAL) == "0x8894[" + "1" * 32 + "]d4e3"
