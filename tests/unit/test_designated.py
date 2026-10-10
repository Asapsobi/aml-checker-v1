"""Designated entities (methodology §13.3–13.4, D-100): a busy wallet whose public explorer tag
names an entity a sanctions list designates is that entity's; so is a deposit address sweeping to
it."""

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx

from amlcheck.config import Intel, Network, Settings, Trace
from amlcheck.core.address import detect
from amlcheck.core.clock import fixed, to_db
from amlcheck.core.models import Chain, Severity, SourceStatus
from amlcheck.core.risk import from_trace
from amlcheck.intel.designations import DESIGNATIONS, designation_for
from amlcheck.intel.names import entity_name
from amlcheck.intel.store import IntelStore, NewLabel
from amlcheck.intel.tags import TagCache, TronscanTags
from amlcheck.net.http import Http, Mode, SourceError
from amlcheck.profile.classifier import CLASSIFIER_VERSION
from amlcheck.screening.designated import DesignatedSource
from amlcheck.storage.db import open_db
from amlcheck.trace.adapter import summary
from amlcheck.trace.model import Trace as Trace_
from tests.unit.trace_world import NOW, Fake, T, addr, engine, tr

FIXTURES = Path(__file__).parents[1] / "fixtures" / "tronscan"
HTX = "TFTWNgDBkQ5wQoP8RXpRznnHvAVV8x5jLu"  # Tronscan: "HTX 4" (VS-23)
URL = "https://apilist.tronscanapi.com/api/account/tag"


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path / "t.db")


@dataclass
class FakeTags:
    tags: dict[str, str]
    fail: set[str] = field(default_factory=set)
    asked: list[str] = field(default_factory=list)
    source: str = "tronscan"

    async def public_tag(self, address: str) -> str:
        self.asked.append(address)
        if address in self.fail:
            raise SourceError("tronscan", "HTTP 503 after 3 tries")
        return self.tags.get(address, "")


def cache(conn: sqlite3.Connection, fake: FakeTags, chain: Chain = Chain.BSC) -> TagCache:
    return TagCache(conn, {chain: fake}, Intel(), clock=fixed(NOW))


# --- which entity a tag names ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("tag", "entity"),
    [
        ("HTX 4", "HTX"),
        ("Huobi-Hot 12", "HTX"),
        ("nobitex.ir hot", "Nobitex"),
        ("Aban Tether", "Aban Tether"),
        ("EXMO", "EXMO"),
        ("EXMOney", None),
        ("Binance-Hot 7", None),
        ("", None),
        (None, None),
    ],
)
def test_a_tag_names_a_designated_entity_by_a_whole_word(tag: str | None, entity: str) -> None:
    found = designation_for(tag)
    assert (found.entity if found else None) == entity


def test_every_designation_cites_a_list_entry() -> None:
    aliases = [a.lower() for d in DESIGNATIONS for a in d.aliases]
    assert len(aliases) == len(set(aliases))  # one entity per alias
    for d in DESIGNATIONS:
        assert d.basis
        assert all(b.startswith(("OFAC SDN ", "UK sanctions ")) for b in d.basis)
    assert designation_for("HTX 5").text == "HTX (UK sanctions RUS3619)"  # type: ignore[union-attr]


# --- Tronscan's answer, recorded (VS-21, VS-23) ----------------------------------------------


@respx.mock
async def test_tronscan_public_tag() -> None:
    respx.get(URL, params={"address": HTX}).respond(
        json=json.loads((FIXTURES / "account_tag_htx.json").read_text())
    )
    untagged = "TQrY8tryqsYVCYS3MFbtffiPp2ccyn4STm"
    respx.get(URL, params={"address": untagged}).respond(
        json=json.loads((FIXTURES / "account_tag_ofac_xinbi.json").read_text())
    )
    async with httpx.AsyncClient() as client:
        tags = TronscanTags(
            Http(client, Network(), mode=Mode.CHECK), Intel(), api_key="k", limiter=None
        )
        assert await tags.public_tag(HTX) == "HTX 4"
        assert await tags.public_tag(untagged) == ""  # no publicTag at all
    assert respx.calls[0].request.headers["TRON-PRO-API-KEY"] == "k"


# --- the cache ---------------------------------------------------------------------------------


async def test_a_tag_is_cached_for_tag_days(conn: sqlite3.Connection) -> None:
    fake = FakeTags({"w": "HTX 4"})
    tags = cache(conn, fake)
    assert await tags.public_tag(Chain.BSC, "w") == "HTX 4"
    assert await tags.public_tag(Chain.BSC, "w") == "HTX 4"
    assert fake.asked == ["w"]
    later = TagCache(conn, {Chain.BSC: fake}, Intel(), clock=fixed(NOW + timedelta(days=7)))
    fake.tags["w"] = "HTX 9"
    assert await later.public_tag(Chain.BSC, "w") == "HTX 9"  # looked up again after 7 days
    assert fake.asked == ["w", "w"]


async def test_a_failed_lookup_is_no_tag_and_keeps_what_was_stored(
    conn: sqlite3.Connection,
) -> None:
    fake = FakeTags({"w": "HTX 4"}, fail={"x"})
    tags = cache(conn, fake)
    assert await tags.public_tag(Chain.BSC, "x") is None
    assert tags.failures == 1
    await tags.public_tag(Chain.BSC, "w")
    fake.fail.add("w")
    later = TagCache(conn, {Chain.BSC: fake}, Intel(), clock=fixed(NOW + timedelta(days=8)))
    assert await later.public_tag(Chain.BSC, "w") == "HTX 4"
    assert later.failures == 1
    assert await cache(conn, fake, Chain.TRON).public_tag(Chain.BSC, "y") is None  # no lookup


async def test_after_two_failures_a_check_asks_no_more(conn: sqlite3.Connection) -> None:
    fake = FakeTags({"ok": "HTX 4"}, fail={"a", "b"})
    tags = cache(conn, fake)
    for a in ("a", "b", "ok"):
        await tags.public_tag(Chain.BSC, a)
    assert fake.asked == ["a", "b"]  # a hanging provider can't eat the time budget
    assert tags.failures == 3  # the skipped one is a gap too


# --- the trace -----------------------------------------------------------------------------


def busy_world() -> tuple[list[tr], str]:  # type: ignore[valid-type]
    """T got 1,000 USDT from a busy wallet: 5 transfers in its window, a hub at 3."""
    hub = addr("hub")
    xs = [tr(5, hub, T, 1000)] + [tr(6 + n / 10, addr(f"ok{n}"), hub, 300, n) for n in range(4)]
    return xs, hub


async def test_a_busy_wallet_tagged_by_a_designated_entity(conn: sqlite3.Connection) -> None:
    xs, hub = busy_world()
    eng, _ = engine(
        conn,
        Fake(xs),
        Settings(trace=Trace(hub_transfers=3)),
        tags=cache(conn, FakeTags({hub: "HTX 4"})),
    )
    t = await eng.run(detect(T))
    (node,) = [n for n in t.nodes if n.address == hub]
    assert (node.terminal, node.test, node.entity) == (
        "sanctioned_entity",
        8,
        "HTX (UK sanctions RUS3619)",
    )
    (e,) = from_trace(t, lambda a, c: "not used")
    assert (e.hop, e.category, e.entity, e.risk_type) == (
        1,
        "sanctioned_entity",
        "HTX (UK sanctions RUS3619)",
        "sanctioned_entity",
    )
    assert (e.percent, e.confidence) == (Decimal(1), None)  # the tag names it: not inferred
    assert Trace_.from_json(t.to_json()).nodes == t.nodes  # `entity` is kept


async def test_without_tags_a_busy_wallet_stays_a_service(conn: sqlite3.Connection) -> None:
    xs, hub = busy_world()
    off = Settings(trace=Trace(hub_transfers=3), intel=Intel(tags=False))
    fake = FakeTags({hub: "HTX 4"})
    eng, _ = engine(conn, Fake(xs), off, tags=cache(conn, fake))  # `[intel] tags = false`
    t = await eng.run(detect(T))
    assert [n.terminal for n in t.nodes if n.address == hub] == ["service_unattributed"]
    assert fake.asked == []
    assert "entity" not in json.dumps(t.to_json())  # traces without it read and hash as before


async def test_an_untagged_or_unknown_busy_wallet(conn: sqlite3.Connection) -> None:
    xs, hub = busy_world()
    eng, _ = engine(
        conn,
        Fake(xs),
        Settings(trace=Trace(hub_transfers=3)),
        tags=cache(conn, FakeTags({hub: "Binance-Hot 7"})),
    )
    t = await eng.run(detect(T))
    assert [n.terminal for n in t.nodes if n.address == hub] == ["service_unattributed"]


async def test_an_operator_named_wallet_keeps_its_name(conn: sqlite3.Connection) -> None:
    xs, hub = busy_world()
    eng, store = engine(
        conn, Fake(xs), Settings(trace=Trace(hub_transfers=3)), cache(conn, FakeTags({hub: "HTX"}))
    )
    named = store.create_entity(Chain.BSC, "Our broker", "otc_desk", named_by="sobhan")
    store.link(Chain.BSC, hub, named, "hub", {"why": "operator"}, provenance="operator")
    t = await eng.run(detect(T))
    assert [n.terminal for n in t.nodes if n.address == hub] == ["otc_desk"]


async def test_a_failed_tag_lookup_is_counted_not_fatal(conn: sqlite3.Connection) -> None:
    xs, hub = busy_world()
    eng, _ = engine(
        conn,
        Fake(xs),
        Settings(trace=Trace(hub_transfers=3)),
        tags=cache(conn, FakeTags({}, fail={hub})),
    )
    t = await eng.run(detect(T))
    assert t.complete
    assert [n.terminal for n in t.nodes if n.address == hub] == ["service_unattributed"]
    assert t.annotations["tag_failures"] == 1
    assert summary(t)["tag_failures"] == 1  # in the check record: the gap is shown
    assert "tag_failures" not in summary(Trace_.from_json({**t.to_json(), "annotations": {}}))


async def test_a_deposit_address_is_the_entity_of_its_sweep_target(
    conn: sqlite3.Connection,
) -> None:
    """A deposit address classified before (test 5) sweeps to a wallet tagged Nobitex: it is
    Nobitex's, inferred at the deposit classification's confidence."""
    dep, hot = addr("dep"), addr("hot")
    features = {
        "profile": {"top_recipient": hot},
        "conditions": ["top_recipient_share_out ≥ 0.9"],
        "bonuses": [],
    }
    conn.execute(
        "INSERT INTO classifications (chain, address_norm, type, is_primary, confidence, "
        "features_json, window_since, window_until, classifier_version, computed_at, expires_at) "
        "VALUES ('bsc', ?, 'DEPOSIT', 1, '0.8', ?, ?, ?, ?, ?, ?)",
        (
            dep,
            json.dumps(features),
            to_db(NOW - timedelta(days=30)),
            to_db(NOW),
            CLASSIFIER_VERSION,
            to_db(NOW),
            to_db(NOW + timedelta(days=14)),
        ),
    )
    fake = FakeTags({hot: "nobitex.ir hot"})
    eng, _ = engine(conn, Fake([tr(5, dep, T, 1000)]), Settings(), tags=cache(conn, fake))
    t = await eng.run(detect(T))
    (node,) = [n for n in t.nodes if n.address == dep]
    assert (node.terminal, node.test, node.entity) == (
        "sanctioned_entity",
        5,
        "Nobitex (OFAC SDN 56981)",
    )
    assert fake.asked == [hot]  # the sweep target's tag, not the deposit address's
    (e,) = from_trace(t, lambda a, c: "not used")
    assert (e.category, e.confidence, e.inferred) == ("sanctioned_entity", Decimal("0.8"), True)


# --- the checked address itself, and names -----------------------------------------------------


async def test_the_checked_address_is_a_designated_wallet(conn: sqlite3.Connection) -> None:
    src = DesignatedSource(
        conn, cache(conn, FakeTags({HTX: "HTX 4"}), Chain.TRON), clock=fixed(NOW)
    )
    r = await src.check(detect(HTX))
    (f,) = r.findings
    assert (f.rule_id, f.severity, r.required) == ("R-SAN-02", Severity.REVIEW, False)
    assert f.evidence == {
        "entity": "HTX",
        "basis": ["UK sanctions RUS3619"],
        "tag": "HTX 4",
        "tag_source": "tronscan",
        "tag_fetched_at": to_db(NOW),
    }
    (e,) = r.evidence["exposures"]
    assert (e["exposure_type"], e["percent"], e["risk_type"], e["entity"]) == (
        "direct",
        "1",
        "sanctioned_entity",
        "HTX (UK sanctions RUS3619)",
    )


async def test_an_untagged_or_unreachable_checked_address(conn: sqlite3.Connection) -> None:
    other = "TQrY8tryqsYVCYS3MFbtffiPp2ccyn4STm"
    tags = cache(conn, FakeTags({other: "Binance 2"}, fail={HTX}), Chain.TRON)
    src = DesignatedSource(conn, tags, clock=fixed(NOW))
    ok = await src.check(detect(other))
    assert (ok.status, ok.findings, ok.detail) == (
        SourceStatus.OK,
        (),
        "tag 'Binance 2': not designated",
    )
    down = await src.check(detect(HTX))
    assert (down.status, down.required, down.findings) == (SourceStatus.ERROR, False, ())


async def test_names_by_stored_tag_or_operator_label(conn: sqlite3.Connection) -> None:
    await cache(conn, FakeTags({HTX: "HTX 4"}), Chain.TRON).public_tag(Chain.TRON, HTX)
    assert entity_name(conn, Chain.TRON, HTX, "sanctioned_entity") == "HTX (UK sanctions RUS3619)"
    known = "TQrY8tryqsYVCYS3MFbtffiPp2ccyn4STm"
    IntelStore(conn, clock=fixed(NOW)).add_label(
        NewLabel(
            Chain.TRON, known, "sanctioned_entity", "operator", "operator:sobhan", note="Nobitex"
        )
    )
    assert entity_name(conn, Chain.TRON, known, "sanctioned_entity") == "Nobitex"


async def test_the_operators_label_on_the_checked_address_decides_first(
    conn: sqlite3.Connection,
) -> None:
    """MistTrack's Nobitex hot wallet has no Tronscan tag: the operator's label makes it R-SAN-02,
    with no lookup at all; without tags, labels still work (BSC, or no key)."""
    nobitex = "TS4htjaSHTZTx8EBYEjNZ5bxywFTXupfAX"
    IntelStore(conn, clock=fixed(NOW)).add_label(
        NewLabel(
            Chain.TRON, nobitex, "sanctioned_entity", "operator", "operator:sobhan", note="Nobitex"
        )
    )
    IntelStore(conn, clock=fixed(NOW)).add_label(  # an earlier one without a note doesn't win
        NewLabel(Chain.TRON, nobitex, "sanctioned_entity", "import", "pack:p", licence="l")
    )
    fake = FakeTags({})
    for tags in (cache(conn, fake, Chain.TRON), None):
        r = await DesignatedSource(conn, tags, clock=fixed(NOW)).check(detect(nobitex))
        (f,) = r.findings
        assert (f.rule_id, f.evidence["entity"], f.evidence["provenance"]) == (
            "R-SAN-02",
            "Nobitex",
            "operator",
        )
        assert r.evidence["exposures"][0]["entity"] == "Nobitex"
    assert fake.asked == []
    plain = await DesignatedSource(conn, None, clock=fixed(NOW)).check(detect(HTX))
    assert (plain.status, plain.findings) == (SourceStatus.OK, ())
