from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path

import pytest

from amlcheck.calibration import Expect, Golden, Recorded, dump, load, markdown, measure, replay
from amlcheck.config import Classifier
from amlcheck.profile import classifier as clf
from amlcheck.profile.classifier import ClassifyContext
from amlcheck.profile.features import Profile
from tests.unit.test_classifier import NOW, S, base, collector, deposit

GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "golden.json"


def golden(
    name: str,
    profile_: Profile,
    expect: Expect,
    *,
    ctx: ClassifyContext | None = None,
    verdict: str = "NO_HITS",
    band: str = "low",
    score: int = 5,
) -> Golden:
    p = replace(profile_, address=name)
    c = ctx or ClassifyContext()
    types = [x.type for x in clf.classify(p, c, S, NOW)]
    return Golden(
        "bsc",
        name,
        expect,
        "test",
        recorded=Recorded(
            at=NOW.isoformat().replace("+00:00", "Z"),
            classifier_version=clf.CLASSIFIER_VERSION,
            profile=clf.profile_json(p),
            context=asdict(c),
            types=types,
            verdict=verdict,
            score=score,
            band=band,
            lower_bound=False,
            traced=False,
            check_id="c-" + name,
        ),
    )


HUB_CTX = ClassifyContext(top_recipient_is_hub=True)


def entries() -> list[Golden]:
    return [
        golden("hub1", base(distinct_senders=400, distinct_recipients=200), Expect(type="HUB")),
        golden("hub2", base(distinct_senders=300, distinct_recipients=300), Expect(type="HUB")),
        golden("dep1", deposit(), Expect(type="DEPOSIT"), ctx=HUB_CTX),
        golden("dep2", deposit(), Expect(type="PERSONAL"), ctx=HUB_CTX),  # a deposit that isn't
        golden("col1", collector(distinct_senders=120), Expect(type="COLLECTOR")),
        golden("ofac", base(), Expect(verdict="BLOCK"), verdict="BLOCK", band="severe", score=100),
        golden("clean1", base(), Expect(clean=True), band="medium", score=38),
        golden("clean2", base(), Expect(clean=True, verdict="NO_HITS"), band="high", score=55),
    ]


def test_replay_matches_what_was_recorded() -> None:
    for g in entries():
        assert g.recorded is not None
        assert replay(g, S) == g.recorded.types


def test_measure() -> None:
    m = measure(entries(), S)
    assert (m.entries, m.recorded) == (8, 8)
    assert m.types["HUB"].precision == Decimal("1.000")
    assert (m.types["DEPOSIT"].predicted, m.types["DEPOSIT"].correct) == (2, 1)
    assert m.types["DEPOSIT"].precision == Decimal("0.500")
    assert m.types["DEPOSIT"].wrong == ["bsc dep2 (is PERSONAL)"]
    assert m.types["PERSONAL"].missed == ["bsc dep2"]
    assert m.types["COLLECTOR"].precision == Decimal("1.000")
    assert (m.verdict_agree, m.verdict_known) == (2, 2)
    assert m.clean == 2
    assert m.clean_too_high == ["bsc clean2: 55 · high"]
    assert not m.passes()
    report = markdown(m)
    assert "| DEPOSIT | 2 | 1 | 0.500 | ≥ 0.9 | 0 |" in report
    assert "**Targets NOT met.**" in report
    assert "- bsc clean2: 55 · high" in report


def test_a_threshold_change_is_measured_offline() -> None:
    stricter = Classifier(hub_min_counterparties=650)  # neither golden hub reaches it now
    m = measure(entries(), stricter)
    assert "HUB" not in m.types or m.types["HUB"].predicted == 0
    assert m.types["HUB"].missed == ["bsc hub1", "bsc hub2"]


def test_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "golden.json"
    dump(reversed(entries()), path)
    loaded = load(path)
    assert loaded == sorted(entries(), key=lambda g: g.key)
    assert load(tmp_path / "missing.json") == []


# AT-58 (offline part): the committed golden set, replayed with today's classifier and defaults.
def test_at58_golden_set_offline() -> None:
    golden_set = [g for g in load(GOLDEN) if g.recorded is not None]
    if not golden_set:
        pytest.skip("no recorded golden set yet (P11, T-11.01)")
    m = measure(golden_set, S)
    assert m.passes(), markdown(m)
