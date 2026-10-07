"""The benchmark against MistTrack (methodology §14, D-091, D-092, AT-70)."""

import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from amlcheck import benchmark as bm
from amlcheck.core.risk import RISK

FIX = Path(__file__).parent.parent / "fixtures" / "benchmark"
SET = Path(__file__).parent.parent / "benchmark" / "benchmark.json"
TRON = "TVvWhZyLcd2DT2Y78XpyrUS3SyzfLeSsWP"
BSC = "0x0c1e52495a1d1ed21f00f389284d0eb4e0ee1576"


def check(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIX / name).read_text(encoding="utf-8"))
    return data


def recorded(name: str = "check_review.json") -> bm.Recorded:
    return bm.Recorded.from_check(check(name), "2.0.0b1", "cfg")


REVIEW = recorded()  # a real check: REVIEW · 47 · moderate (sanctioned entity, direct 7.8%)
BLOCK = recorded("check_block.json")  # a real check: BLOCK · 100 (NBCTF ASO 07/26)
CLEAN = replace(REVIEW, verdict="NO_HITS", rules=(), exposures=(), score=0, level="low")


def wallet(n: int, expected: str, rec: bm.Recorded | None = REVIEW, **kw: Any) -> bm.Wallet:
    """Wallets are told apart by address; the record can be shared."""
    return bm.Wallet(
        "tron", f"T{n:033d}", expected, "owner, MistTrack, 2026-10-08", recorded=rec, **kw
    )


def test_a_record_gives_back_its_score() -> None:
    """§14.3: with the settings it was recorded under, a record reproduces its check's score."""
    assert REVIEW.scored() == (47, "moderate")
    assert (REVIEW.verdict, REVIEW.rules) == ("REVIEW", ("R-EXP-01", "R-EXP-02", "R-SCR-01"))
    assert BLOCK.scored() == (100, "severe")
    assert REVIEW.risks()[0] == "Sanctioned entity: direct received 7.8%"
    assert REVIEW.coverage_in is not None
    assert bm.unreproduced([wallet(1, "moderate"), wallet(2, "severe", BLOCK)]) == []
    tampered = wallet(3, "moderate", replace(REVIEW, score=60))
    assert bm.unreproduced([tampered]) == [tampered]


def test_a_check_without_a_v2_score_is_refused() -> None:
    data = check("check_review.json")
    with pytest.raises(ValueError, match="no version 2 score"):
        bm.Recorded.from_check({**data, "score": None}, "2.0.0b1", "cfg")


# AT-70 (D-091): same level, within one, missed, over; the pass line.
def test_at70_agreement() -> None:
    a = bm.measure(
        [
            wallet(1, "moderate"),  # same
            wallet(2, "severe", BLOCK),  # same
            wallet(3, "high"),  # ours a level lower
            wallet(4, "high", CLEAN),  # missed: two levels apart
            wallet(5, "low", BLOCK),  # over: three levels apart
            wallet(6, "low", None),  # not recorded yet: not counted
        ]
    )
    assert len(a.rows) == 5
    assert (a.same, a.share) == (2, Decimal("0.4"))
    assert [r.apart for r in a.rows] == [0, 0, -1, -2, 3]
    assert [r.wallet.address[-1] for r in a.far] == ["4", "5"]
    assert [r.missed for r in a.rows] == [False, False, False, True, False]
    assert [r.over for r in a.rows] == [False, False, False, False, True]
    assert not a.passed


def test_at70_passes_at_70_percent_within_one_and_none_missed() -> None:
    same = [wallet(n, "moderate") for n in range(7)]
    near = [wallet(n, "high") for n in range(7, 10)]
    assert bm.measure(same + near).passed  # 70%, all within one
    assert not bm.measure(same[:6] + near).passed  # 6 of 9: 67%
    missed = wallet(10, "high", CLEAN)
    assert not bm.measure([*same, missed]).passed  # 7 of 8 same, but missed and two levels off
    assert bm.measure([missed]).unexcused == list(bm.measure([missed]).rows)
    excused = replace(missed, cause="unknowable", reason="a private MistTrack label")
    assert bm.measure([excused]).unexcused == []


def test_a_lower_bound_is_marked() -> None:
    row = bm.measure([wallet(1, "moderate", replace(REVIEW, verdict="INCOMPLETE"))]).rows[0]
    assert row.lower_bound
    assert "≥ 47 · moderate (INCOMPLETE)" in bm.markdown(bm.measure([row.wallet]))


# D-092: a candidate is adopted only if it fixes at least 2 mismatches and breaks none.
def test_a_candidate_is_adopted_for_patterns_only() -> None:
    lows = [wallet(1, "low"), wallet(2, "low")]  # MistTrack: low; ours: moderate
    keeper = wallet(3, "moderate")
    now = bm.measure([*lows, keeper])
    softer = bm.measure([*lows, keeper], k=Decimal(4), decay=Decimal("0.4"))
    assert [r.level for r in softer.rows] == ["low", "low", "low"]  # 47 → 27 at k = 4
    fixed, broken = bm.changes(now, softer)
    assert ([r.wallet.address[-1] for r in fixed], [r.wallet.address[-1] for r in broken]) == (
        ["1", "2"],
        ["3"],
    )
    assert not bm.adoptable(fixed, broken)
    fixed, broken = bm.changes(bm.measure(lows), bm.measure(lows, k=Decimal(4)))
    assert bm.adoptable(fixed, broken)
    one = bm.changes(bm.measure(lows[:1]), bm.measure(lows[:1], k=Decimal(4)))
    assert not bm.adoptable(*one)  # fixes 1


def test_candidate_weights_are_tried_offline() -> None:
    lighter = REVIEW.scored(weights={"sanctioned": Decimal("0.5")})
    assert lighter[0] < 47
    assert RISK["sanctioned"][1] == Decimal("1.0")  # the table itself is untouched
    assert BLOCK.scored(weights={"sanctioned": Decimal(0)}) == (100, "severe")  # BLOCK stays 100


def test_read_levels_keeps_only_the_level(tmp_path: Path) -> None:
    f = tmp_path / "levels.csv"
    f.write_text(
        "Address,Chain,Level,Score,Risk types,Note\n"
        f"{TRON},tron,Moderate,55,Sanctioned entity,our counterparty\n"
        f"{BSC},,low,10,,\n",
        encoding="utf-8",
    )
    wallets, ignored = bm.read_levels(f, "2026-10-08")
    assert ignored == ["risk types", "score"]
    assert [(w.chain, w.address, w.expected, w.note) for w in wallets] == [
        ("tron", TRON, "moderate", "our counterparty"),
        ("bsc", BSC, "low", None),
    ]
    assert wallets[0].source == "owner, MistTrack, 2026-10-08"
    assert "55" not in json.dumps([w.__dict__ for w in wallets], default=str)  # no score kept
    f.write_text(f"address,level\n{TRON},medium\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"line 2: .*level must be one of low, moderate"):
        bm.read_levels(f, "2026-10-08")
    f.write_text(f"wallet,level\n{TRON},low\n", encoding="utf-8")
    with pytest.raises(ValueError, match="needs the columns address and level"):
        bm.read_levels(f, "2026-10-08")


def test_the_set_round_trips_and_merges(tmp_path: Path) -> None:
    path = tmp_path / "b.json"
    first = [wallet(1, "high", cause="gap", reason="a missed hop"), wallet(2, "low", None)]
    bm.dump(first, path)
    assert bm.load(path) == first
    again = bm.merge(bm.load(path), [wallet(1, "high", None)])  # the same level, looked up again
    one = next(w for w in again if w.address.endswith("1"))
    assert (one.cause, one.reason, one.recorded) == ("gap", "a missed hop", REVIEW)
    relooked = wallet(1, "moderate", None)  # a new level: the old reason explained another one
    merged = bm.merge(bm.load(path), [relooked, wallet(3, "severe", None)])
    one = next(w for w in merged if w.address.endswith("1"))
    assert (one.expected, one.cause, one.reason, one.recorded) == ("moderate", None, None, REVIEW)
    assert len(merged) == 3


def test_the_report() -> None:
    wallets = [
        wallet(1, "moderate"),
        wallet(2, "high"),
        wallet(3, "severe", BLOCK, cause="disputed", reason="x"),
    ]
    text = bm.markdown(bm.measure(wallets), bm.measure(wallets, k=Decimal(12)))
    assert "3 wallet(s): same level 2 (67%), within one level 3, missed 0, over 0." in text
    assert "**AT-70: not passed**" in text
    assert "| moderate | 0 | 1 | 0 | 0 |" in text  # the confusion matrix
    assert "| high | 0 | 1 | 0 | 0 |" in text
    assert "**to explain**" in text  # wallet 2 disagrees and has no reason yet
    assert "## Candidate: k = 12" in text
    assert "Adoptable under D-092: no" in text  # k = 12 fixes only wallet 2
    assert bm.markdown(bm.measure([])).endswith("No wallet recorded yet (Q-37).\n")


def test_the_recorded_set_reproduces() -> None:
    """Every record in the set gives back its score with its own settings (§14.3)."""
    assert bm.unreproduced(bm.load(SET)) == []
