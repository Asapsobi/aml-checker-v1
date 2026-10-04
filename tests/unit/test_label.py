"""The checked address's own label (methodology §11.6, AT-65)."""

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from amlcheck.config import Settings
from amlcheck.core.address import detect
from amlcheck.core.audit import verify
from amlcheck.core.clock import fixed, to_db
from amlcheck.core.engine import screen
from amlcheck.core.models import Address, Chain, SourceResult, SourceStatus
from amlcheck.intel.names import address_label, label_text
from amlcheck.intel.store import IntelStore
from amlcheck.monitor import wallets
from amlcheck.screening.base import SourceHealth
from amlcheck.storage.db import open_db
from tests.unit.trace_world import NOW

TRON = "TNHrhtVnRMRaTJFRsqLUqSytCKsavXJJaa"
OWN = "TNPeeaaFB7K9cmo4uQpcU32zGK8G1NYqeL"


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = open_db(tmp_path / "a.db")
    c.execute(
        "INSERT INTO list_snapshots (id, source, fetched_at, published_at, sha256, entry_count, "
        "address_count) VALUES (1, 'ofac_sdn', ?, '2026-09-30', 'h', 1, 1)",
        (to_db(NOW),),
    )
    c.execute(
        "INSERT INTO sanctioned_addresses VALUES (1, ?, 'USDT', '22985', 'CHEIL CREDIT BANK', "
        "'DPRK4', 1)",
        ("TA3941uFAvmVibSkQ6fMJXxmaSNovX86mz",),
    )
    return c


class Classified:
    """The classifier source's evidence: a primary type with its confidence."""

    source = "classifier"
    label = "Address classifier"
    required = False
    timeout: float | None = None

    def __init__(self, kind: str, confidence: str) -> None:
        self.types = [{"type": kind, "confidence": confidence, "primary": True}]

    async def check(self, address: Address) -> SourceResult:
        return SourceResult(
            self.source, self.label, False, SourceStatus.OK, NOW, (), "ok", {"types": self.types}
        )

    async def health(self) -> SourceHealth:
        return SourceHealth(self.source, self.label, SourceStatus.OK, None, "test")


async def labelled(conn: sqlite3.Connection, address: str) -> dict[str, Any] | None:
    r = await screen(
        detect(address),
        [Classified("PERSONAL", "0.5")],
        conn=conn,
        settings=Settings(),
        now=fixed(NOW),
    )
    stored = conn.execute(
        "SELECT label_json FROM checks WHERE check_id = ?", (r.check_id,)
    ).fetchone()[0]
    assert (json.loads(stored) if stored else None) == r.label
    return r.label


# AT-65: an OFAC address; an own wallet; an unknown personal wallet.
async def test_at65_labels_in_order(conn: sqlite3.Connection) -> None:
    ofac = await labelled(conn, "TA3941uFAvmVibSkQ6fMJXxmaSNovX86mz")
    assert label_text(ofac) == "OFAC SDN: CHEIL CREDIT BANK"
    assert ofac is not None
    assert (ofac["source"], ofac["category"], ofac["inferred"]) == (
        "sanctions",
        "sanctioned",
        False,
    )
    wallets.add(conn, IntelStore(conn), detect(OWN), "TRON hot wallet", now=NOW, by="sobhan")
    assert label_text(await labelled(conn, OWN)) == "own wallet: TRON hot wallet"
    personal = await labelled(conn, TRON)
    assert label_text(personal) == "PERSONAL (inferred, 0.5)"
    assert personal is not None
    assert (personal["inferred"], personal["confidence"]) == (True, "0.5")
    assert verify(conn).ok  # the label is hashed with the record


def test_freeze_entity_and_label(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO issuer_events VALUES ('tron', 'TR7N', ?, 'AddedBlackList', NULL, 'e1', 0, 5, "
        "'2026-01-01T00:00:00.000000Z')",
        (TRON,),
    )
    assert label_text(address_label(conn, Chain.TRON, TRON)) == "Tether-frozen"
    conn.execute(
        "INSERT INTO issuer_events VALUES ('tron', 'TR7N', ?, 'RemovedBlackList', NULL, 'e2', 0, "
        "6, '2026-02-01T00:00:00.000000Z')",
        (TRON,),
    )
    assert address_label(conn, Chain.TRON, TRON) is None  # released: not frozen now
    store = IntelStore(conn, clock=fixed(NOW))
    entity = store.create_entity(Chain.TRON, "auto", "unknown")
    store.link(Chain.TRON, TRON, entity, "deposit", {})
    store.name_entity(entity, "Binance", "exchange_regulated", "sobhan")
    assert label_text(address_label(conn, Chain.TRON, TRON)) == "Binance (exchange_regulated)"


def test_no_label() -> None:
    assert label_text(None) is None
