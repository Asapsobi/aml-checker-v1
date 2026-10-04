import sqlite3
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from amlcheck.cases import cases, decisions
from amlcheck.cases.cases import CaseError
from amlcheck.config import Settings
from amlcheck.core.address import detect
from amlcheck.core.audit import verify as verify_checks
from amlcheck.core.clock import fixed
from amlcheck.core.engine import screen
from amlcheck.core.models import Chain
from amlcheck.intel.store import IntelStore
from amlcheck.profile import classifier as clf
from amlcheck.profile.classifier import ClassifyContext
from amlcheck.storage.db import open_db
from tests.unit.test_classifier import S, collector, deposit
from tests.unit.test_engine import Fake as Source
from tests.unit.trace_world import NOW, Fake, T, addr, engine, tr

X = "0x" + "77" * 20
Y = "0x" + "88" * 20


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / "a.db")


async def checked(conn: sqlite3.Connection, address: str, *rules: str) -> str:
    r = await screen(
        detect(address),
        [Source("exposure", rules=rules)],
        conn=conn,
        settings=Settings(),
        client="acme",
        now=fixed(NOW),
    )
    return r.check_id


def opened(conn: sqlite3.Connection, address: str) -> cases.Case:
    case, new = cases.open_case(conn, detect(address), by="sobhan", now=NOW)
    assert new
    return case


async def test_open_rules(conn: sqlite3.Connection) -> None:
    review = await checked(conn, X, "R-EXP-01")
    await checked(conn, Y)  # NO_HITS
    with pytest.raises(CaseError, match="set \\[operator\\] name"):
        cases.open_case(conn, detect(X), by="", now=NOW)
    case, new = cases.open_case(conn, detect(X), by="sobhan", now=NOW)
    assert new
    assert (case.opened_from, case.status, case.client, case.opened_by) == (
        review,
        "open",
        "acme",
        "sobhan",
    )
    again, new2 = cases.open_case(conn, detect(X), by="ali", now=NOW)
    assert (again.case_id, new2) == (case.case_id, False)  # one open case per address
    with pytest.raises(CaseError, match="NO_HITS: cases are for REVIEW, BLOCK or INCOMPLETE"):
        cases.open_case(conn, detect(Y), by="sobhan", now=NOW)
    with pytest.raises(CaseError, match="never been checked"):
        cases.open_case(conn, detect("0x" + "99" * 20), by="sobhan", now=NOW)
    with pytest.raises(CaseError, match="is for"):
        cases.open_case(conn, detect(Y), by="sobhan", now=NOW, check_id=review)
    assert cases.find(conn, case.case_id[:8]) == case
    with pytest.raises(CaseError, match="no case"):
        cases.find(conn, "nope")


async def test_decide_lifecycle(conn: sqlite3.Connection) -> None:
    await checked(conn, X, "R-EXP-01")
    case = opened(conn, X)

    def decide(kind: str, note: str = "looked at the counterparties", by: str = "sobhan") -> None:
        cases.decide(conn, case, kind, note, by=by, now=NOW, tool_version="0.9.0")

    with pytest.raises(CaseError, match="needs a note"):
        decide("approved", note="  ")
    with pytest.raises(CaseError, match="one of approved, rejected, escalated"):
        decide("maybe")
    with pytest.raises(CaseError, match="set \\[operator\\] name"):
        decide("approved", by="")
    decide("escalated", note="needs the compliance lead")
    assert cases.get(conn, case.case_id).status == "open"  # type: ignore[union-attr]
    decide("approved", note="known OTC client, payout matches the invoice")
    assert cases.get(conn, case.case_id).status == "closed"  # type: ignore[union-attr]
    with pytest.raises(CaseError, match="is closed"):
        decide("rejected")
    chain = decisions.stored(conn, case_id=case.case_id)
    assert [s.decision.decision for s in chain] == ["escalated", "approved"]
    record_hash = conn.execute(
        "SELECT record_hash FROM checks WHERE check_id = ?", (case.opened_from,)
    ).fetchone()[0]
    assert {s.decision.check_record_hash for s in chain} == {record_hash}
    assert chain[1].prev_hash == chain[0].record_hash
    assert decisions.verify(conn).ok
    reopened, new = cases.open_case(conn, detect(X), by="sobhan", now=NOW)
    assert new
    assert reopened.case_id != case.case_id  # a new question is a new case


# AT-49: tamper one decisions row → the decision chain reports the break; the check chain is intact.
async def test_at49_tampered_decision(conn: sqlite3.Connection) -> None:
    await checked(conn, X, "R-EXP-01")
    case = opened(conn, X)
    for kind in ("escalated", "approved"):
        cases.decide(conn, case, kind, f"{kind} note", by="sobhan", now=NOW, tool_version="0.9.0")
    conn.execute("UPDATE decisions SET note = 'nothing to see' WHERE seq = 1")
    v = decisions.verify(conn)
    assert not v.ok
    assert v.break_at == 1
    assert v.reason == "contents do not match record_hash"
    assert verify_checks(conn).ok  # the check chain is untouched


async def test_decision_tied_to_its_check_record(conn: sqlite3.Connection) -> None:
    await checked(conn, X, "R-EXP-01")
    case = opened(conn, X)
    cases.decide(conn, case, "approved", "ok", by="sobhan", now=NOW, tool_version="0.9.0")
    conn.execute(
        "UPDATE checks SET client = 'someone else' WHERE check_id = ?", (case.opened_from,)
    )
    v = decisions.verify(conn)
    assert not v.ok
    assert "the check record it was made on has changed" in (v.reason or "")


def classify_x_as_collector(conn: sqlite3.Connection) -> None:
    p = collector(address=X, distinct_senders=120, small_in_share=Decimal("0.92"))
    cs = clf.classify(p, ClassifyContext(), S, NOW, clf.suppressed(conn, Chain.BSC, X))
    clf.save(conn, Chain.BSC, p, cs, S, NOW)


# AT-50: reject COLLECTOR for X; reclassify; bump classifier_version → suppressed, then returns.
async def test_at50_rejected_type_suppressed_until_version_changes(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    classify_x_as_collector(conn)
    assert "COLLECTOR" in cases.latest_types(conn, Chain.BSC, X)
    await checked(conn, X, "R-HEU-07", "R-EXP-01")
    case = opened(conn, X)
    store = IntelStore(conn, clock=fixed(NOW))
    with pytest.raises(CaseError, match="not classified DEPOSIT"):
        cases.reject(conn, store, case, "DEPOSIT", by="sobhan", now=NOW)
    cases.reject(conn, store, case, "collector", by="sobhan", now=NOW)
    assert clf.suppressed(conn, Chain.BSC, X) == {"COLLECTOR"}
    assert clf.cached(conn, Chain.BSC, X, NOW) is None  # the stored one is stale now
    classify_x_as_collector(conn)  # reclassify
    assert "COLLECTOR" not in cases.latest_types(conn, Chain.BSC, X)
    monkeypatch.setattr(clf, "CLASSIFIER_VERSION", 2)
    assert clf.suppressed(conn, Chain.BSC, X) == frozenset()
    classify_x_as_collector(conn)
    assert "COLLECTOR" in cases.latest_types(conn, Chain.BSC, X)  # it returns


async def test_confirm_collector_labels_with_a_chosen_category(conn: sqlite3.Connection) -> None:
    classify_x_as_collector(conn)
    await checked(conn, X, "R-HEU-07", "R-EXP-01")
    case = opened(conn, X)
    store = IntelStore(conn, clock=fixed(NOW))
    with pytest.raises(CaseError, match="give --category"):
        cases.confirm(conn, store, case, "COLLECTOR", by="sobhan", now=NOW)
    with pytest.raises(CaseError, match="can only come from"):
        cases.confirm(conn, store, case, "COLLECTOR", by="sobhan", now=NOW, category="sanctioned")
    cases.confirm(conn, store, case, "COLLECTOR", by="sobhan", now=NOW, category="scam")
    (label,) = store.labels(Chain.BSC, X)
    assert (label.category, label.provenance, label.source_ref, label.created_by) == (
        "scam",
        "operator",
        f"case:{case.case_id}",
        "sobhan",
    )
    (fb,) = cases.feedback(conn, case.case_id)
    assert (fb.type, fb.verdict, fb.label_id) == ("COLLECTOR", "confirmed", label.id)
    assert clf.suppressed(conn, Chain.BSC, X) == frozenset()


# AT-51: confirm a deposit, name its entity, re-trace a target it funded → the deposit is a
# terminal at test 4 (no read).
async def test_at51_confirmed_deposit_resolves_locally(conn: sqlite3.Connection) -> None:
    dep, hub = addr("P"), addr("H")
    fake = Fake([tr(5, dep, T, 5000), *(tr(10 + i, addr(f"s{i}"), dep, 1000, i) for i in range(5))])
    eng, store = engine(conn, fake)
    # The classifier found `dep` a DEPOSIT of hub `hub` (P5): inferred membership, unnamed entity.
    p = deposit(address=dep, top_recipient=hub)
    cs = clf.classify(p, ClassifyContext(top_recipient_is_hub=True), S, NOW - timedelta(days=1))
    assert clf.primary(cs).type == "DEPOSIT"  # type: ignore[union-attr]
    clf.save(conn, Chain.BSC, p, cs, S, NOW - timedelta(days=1))
    entity = store.create_entity(Chain.BSC, f"hub-{hub[:8]}")
    store.link(Chain.BSC, hub, entity, "hub", {})
    store.link(Chain.BSC, dep, entity, "deposit", {"classification": "DEPOSIT"})

    before = await eng.run(detect(T))
    node = next(n for n in before.nodes if n.address == dep)
    assert (node.terminal, node.test) == ("service_unattributed", 5)  # a guess from its type

    await checked(conn, dep, "R-EXP-01")
    case = opened(conn, dep)
    cases.confirm(
        conn,
        store,
        case,
        "DEPOSIT",
        by="sobhan",
        now=NOW,
        entity_name="Binance",
        kind="exchange_regulated",
    )
    membership = store.membership(Chain.BSC, dep)
    assert membership is not None
    assert membership[2] == "operator"
    asked = len(fake.asked)
    after = await eng.run(detect(T))
    node = next(n for n in after.nodes if n.address == dep)
    assert (node.terminal, node.test, node.read) == ("exchange_regulated", 4, False)
    assert dep not in fake.asked[asked:]  # resolved from local data, no read
    assert after.partition == {"exchange_regulated": Decimal(1)}


async def test_reject_deposit_unlinks_the_inferred_membership(conn: sqlite3.Connection) -> None:
    dep, hub = X, Y
    store = IntelStore(conn, clock=fixed(NOW))
    p = deposit(address=dep, top_recipient=hub)
    clf.save(
        conn,
        Chain.BSC,
        p,
        clf.classify(p, ClassifyContext(top_recipient_is_hub=True), S, NOW),
        S,
        NOW,
    )
    entity = store.create_entity(Chain.BSC, "hub-x")
    store.link(Chain.BSC, dep, entity, "deposit", {})
    await checked(conn, dep, "R-EXP-01")
    case = opened(conn, dep)
    cases.reject(conn, store, case, "DEPOSIT", by="sobhan", now=NOW)
    assert store.membership(Chain.BSC, dep) is None
    assert clf.suppressed(conn, Chain.BSC, dep) == {"DEPOSIT"}


async def test_confirm_hub_names_its_entity(conn: sqlite3.Connection) -> None:
    store = IntelStore(conn, clock=fixed(NOW))
    p = collector(address=X, distinct_senders=600, distinct_recipients=10)
    clf.save(conn, Chain.BSC, p, clf.classify(p, ClassifyContext(), S, NOW), S, NOW)
    assert "HUB" in cases.latest_types(conn, Chain.BSC, X)
    await checked(conn, X, "R-EXP-01")
    case = opened(conn, X)
    with pytest.raises(CaseError, match="give --name and --kind"):
        cases.confirm(conn, store, case, "HUB", by="sobhan", now=NOW)
    cases.confirm(
        conn, store, case, "HUB", by="sobhan", now=NOW, entity_name="OKX", kind="exchange_regulated"
    )
    e = store.entity_for(Chain.BSC, X)
    assert e is not None
    assert (e.name, e.kind, e.named_by, e.role) == ("OKX", "exchange_regulated", "sobhan", "hub")
