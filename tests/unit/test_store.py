import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from amlcheck.core.clock import fixed
from amlcheck.core.models import Chain
from amlcheck.intel.labels_csv import parse, replace_all
from amlcheck.intel.store import IntelError, IntelStore, NewLabel

NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)
A = "TNHrhtVnRMRaTJFRsqLUqSytCKsavXJJaa"


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    from amlcheck.storage.db import open_db

    return open_db(tmp_path / "a.db")


@pytest.fixture
def store(conn: sqlite3.Connection) -> IntelStore:
    return IntelStore(conn, clock=fixed(NOW))


def op(category: str, address: str = A, **kw: object) -> NewLabel:
    return NewLabel(Chain.TRON, address, category, "operator", "case:1", created_by="sobhan", **kw)  # type: ignore[arg-type]


# AT-31: add then retract an operator label → active then retracted; still listed with --all.
def test_at31_add_then_retract(store: IntelStore) -> None:
    label_id = store.add_label(op("mixer", note="seen in a case"))
    (row,) = store.labels(Chain.TRON, A)
    assert row.active
    assert (row.category, row.provenance, row.created_by) == ("mixer", "operator", "sobhan")
    assert row.confidence == Decimal(1)
    store.retract(label_id, "wrong address", by="sobhan")
    assert store.labels(Chain.TRON, A) == []
    (gone,) = store.labels(Chain.TRON, A, include_retracted=True)
    assert not gone.active
    assert (gone.retract_reason, gone.retracted_by) == ("wrong address", "sobhan")
    with pytest.raises(IntelError, match="already retracted"):
        store.retract(label_id, "again", by=None)


@pytest.mark.parametrize(
    ("label", "message"),
    [
        (op("sanctioned"), "can only come from: list"),
        (op("casino"), "unknown category"),
        (op("mixer", confidence=Decimal("0.5")), "confidence 1"),
        (NewLabel(Chain.TRON, A, "mixer", "import", "pack:x"), "licence"),
        (NewLabel(Chain.TRON, A, "suspicious_collector", "inferred", "clf", Decimal(0)), "above 0"),
    ],
)
def test_refusals(store: IntelStore, label: NewLabel, message: str) -> None:
    with pytest.raises(IntelError, match=message):
        store.add_label(label)


def test_retract_needs_reason_and_existing_label(store: IntelStore) -> None:
    label_id = store.add_label(op("scam"))
    with pytest.raises(IntelError, match="needs a reason"):
        store.retract(label_id, " ", by=None)
    with pytest.raises(IntelError, match="no label #99"):
        store.retract(99, "x", by=None)


def test_best_terminal_human_before_inferred_then_confidence_then_order(store: IntelStore) -> None:
    store.add_label(
        NewLabel(Chain.TRON, A, "suspicious_collector", "inferred", "clf", Decimal("0.9"))
    )
    t = store.best_terminal(Chain.TRON, A)
    assert t is not None
    assert t.category == "suspicious_collector"
    store.add_label(op("gambling"))
    t = store.best_terminal(Chain.TRON, A)
    assert t is not None
    assert t.category == "gambling"  # an operator label beats any inference
    store.add_label(op("scam"))
    t = store.best_terminal(Chain.TRON, A)
    assert t is not None
    assert t.category == "scam"  # same provenance and confidence: category order (6 < 9)


def test_best_terminal_lower_confidence_inference_loses(store: IntelStore) -> None:
    store.add_label(
        NewLabel(Chain.TRON, A, "service_unattributed", "inferred", "a", Decimal("0.6"))
    )
    store.add_label(
        NewLabel(Chain.TRON, A, "contract_unattributed", "inferred", "b", Decimal("0.95"))
    )
    t = store.best_terminal(Chain.TRON, A)
    assert t is not None
    assert t.category == "contract_unattributed"


def test_best_terminal_uses_labels_csv_and_entity_kind(
    store: IntelStore, conn: sqlite3.Connection
) -> None:
    assert store.best_terminal(Chain.TRON, A) is None
    replace_all(
        conn, parse(f"address,chain,tag,note,source\n{A},tron,allowlist,,\n{A},tron,vip,,\n")
    )
    t = store.best_terminal(Chain.TRON, A)
    assert t is not None
    assert (t.category, t.source_ref) == ("own_or_trusted", "labels.csv")
    hub = store.create_entity(Chain.TRON, "Binance", "exchange_regulated", named_by="sobhan")
    store.link(Chain.TRON, A, hub, "deposit", {"why": "test"})
    t = store.best_terminal(Chain.TRON, A)
    assert t is not None
    assert t.category == "exchange_regulated"  # order 15 beats own_or_trusted 17
    assert t.entity_id == hub


def test_retracted_labels_are_not_terminals(store: IntelStore) -> None:
    store.retract(store.add_label(op("darknet")), "mistake", by=None)
    assert store.best_terminal(Chain.TRON, A) is None


def test_entities(store: IntelStore) -> None:
    e1 = store.create_entity(Chain.TRON, "hub-TNHrhtVn")
    e2 = store.create_entity(Chain.TRON, "other")
    store.link(Chain.TRON, A, e1, "hub", {"n": 1})
    ent = store.entity_for(Chain.TRON, A)
    assert ent is not None
    assert (ent.id, ent.kind, ent.role) == (e1, "unknown", "hub")
    store.link(Chain.TRON, A, e2, "member", {}, provenance="operator")  # one entity per address
    ent = store.entity_for(Chain.TRON, A)
    assert ent is not None
    assert ent.id == e2
    store.name_entity(e1, "Binance", "exchange_regulated", by="sobhan")
    named = store.entity(e1)
    assert named is not None
    assert (named.name, named.kind, named.named_by) == ("Binance", "exchange_regulated", "sobhan")
    with pytest.raises(IntelError, match="can't be an entity's kind"):
        store.create_entity(Chain.TRON, "x", "sanctioned")
    with pytest.raises(IntelError, match="role must be"):
        store.link(Chain.TRON, A, e1, "boss", {})
    with pytest.raises(IntelError, match="no entity #77"):
        store.link(Chain.TRON, A, 77, "hub", {})


def test_stats(store: IntelStore) -> None:
    store.add_label(op("mixer"))
    store.retract(store.add_label(op("scam")), "x", by=None)
    s = store.stats()
    assert s["active_labels"] == {"operator": {"mixer": 1}}
    assert s["retracted_labels"] == 1


async def test_exposure_reads_intel_labels(conn: sqlite3.Connection, store: IntelStore) -> None:
    from amlcheck.core.address import detect
    from tests.unit.test_exposure import ME, source, tr

    store.add_label(op("mixer", address="TMIX"))
    store.add_label(op("own_or_trusted", address="TTRUSTED"))
    mixed = await source(conn, [tr(30, "TMIX", "500")]).check(detect(ME))
    assert "R-HEU-05" in {f.rule_id for f in mixed.findings}  # operator-labelled mixer
    conn.execute("DELETE FROM transfers")
    conn.execute("DELETE FROM history_windows")
    trusted = await source(
        conn, [tr(20, "TTRUSTED", "1000"), tr(18, "TOUT", "950", out=True)]
    ).check(detect(ME))
    assert "R-HEU-02" not in {f.rule_id for f in trusted.findings}  # trusted inflow left out
