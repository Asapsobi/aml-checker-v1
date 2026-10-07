"""The benchmark against MistTrack (methodology §14, D-093, D-092, AT-70)."""

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


def wallet(
    n: int, theirs: str, rec: bm.Recorded | None = REVIEW, risks: str = "", **kw: Any
) -> bm.Wallet:
    """Wallets are told apart by address; the record can be shared."""
    return bm.Wallet(
        "tron",
        f"T{n:033d}",
        theirs,
        "MistTrack Light, 2026-10-07",
        bm.parse_risks(risks),
        recorded=rec,
        **kw,
    )


def test_a_record_gives_back_its_score() -> None:
    """§14.3: with the settings it was recorded under, a record reproduces its check's score."""
    assert REVIEW.scored() == (47, "moderate")
    assert (REVIEW.verdict, REVIEW.rules) == ("REVIEW", ("R-EXP-01", "R-EXP-02", "R-SCR-01"))
    assert BLOCK.scored() == (100, "severe")
    assert REVIEW.risks()[0] == "Sanctioned entity: direct received 7.8%"
    assert REVIEW.found() == {"sanctioned_entity", "illicit_activity"}
    assert (REVIEW.own, BLOCK.own) == (False, True)  # BLOCK: R-SAN-01, the address itself
    assert bm.unreproduced([wallet(1, "risky"), wallet(2, "risky", BLOCK)]) == []
    tampered = wallet(3, "risky", replace(REVIEW, score=60))
    assert bm.unreproduced([tampered]) == [tampered]


def test_a_check_without_a_v2_score_is_refused() -> None:
    data = check("check_review.json")
    with pytest.raises(ValueError, match="no version 2 score"):
        bm.Recorded.from_check({**data, "score": None}, "2.0.0b1", "cfg")


def test_misttrack_rows() -> None:
    rows = bm.parse_risks("Illicit Activity/direct/52.07; Sanctioned Entity/indirect/15.91;")
    assert rows == (
        bm.TheirRisk("Illicit Activity", "direct", Decimal("52.07")),
        bm.TheirRisk("Sanctioned Entity", "indirect", Decimal("15.91")),
    )
    assert bm.parse_risks("") == ()
    assert bm.parse_risks("Illicit Activity/direct/100")[0].own
    for bad, why in [
        ("Scam/direct/5", "unknown MistTrack risk type"),
        ("Mixer/both/5", "direct or indirect"),
        ("Mixer/direct/lots", "must be a number"),
        ("Mixer/direct/101", "0 to 100"),
        ("Mixer direct 5", "type/direct or indirect/share"),
    ]:
        with pytest.raises(ValueError, match=why):
            bm.parse_risks(bad)


# AT-70 (D-093): ours low against theirs low; moderate and above against risky.
def test_at70_levels() -> None:
    a = bm.measure(
        [
            wallet(1, "risky"),  # ours moderate: same
            wallet(2, "risky", BLOCK),  # ours severe (BLOCK): same
            wallet(3, "low"),  # ours moderate: mismatch
            wallet(4, "risky", CLEAN),  # ours low: mismatch
            wallet(5, "low", CLEAN),  # same
            wallet(6, "low", None),  # not recorded yet: not counted
        ]
    )
    assert [r.ours for r in a.rows] == ["risky", "risky", "risky", "low", "low"]
    assert (a.same, a.share) == (3, Decimal("0.6"))
    assert [r.wallet.address[-1] for r in a.unexplained] == ["3", "4"]
    assert not a.passed


def test_gaps_and_extras() -> None:
    """A gap is a MistTrack row at ≥ 5% whose risk type the check finds nowhere (§14.2)."""
    risks = (
        "Illicit Activity/direct/52.07; Sanctioned Entity/indirect/15.91; "
        "Risky Exchange/direct/6; Mixer/direct/4"
    )
    row = bm.measure([wallet(1, "risky", REVIEW, risks)]).rows[0]
    assert [g.risk_type for g in row.gaps] == ["Risky Exchange"]  # Mixer is under 5%
    assert row.extras == []
    only = bm.measure([wallet(2, "risky", REVIEW, "Risky Exchange/direct/1")]).rows[0]
    assert only.extras == ["illicit_activity", "sanctioned_entity"]
    # MistTrack flags the wallet itself: matched when the check lists or freezes the address.
    itself = "Illicit Activity/direct/100"
    assert bm.measure([wallet(3, "risky", CLEAN, itself)]).rows[0].gaps != []
    listed = replace(CLEAN, rules=("R-SAN-01",), verdict="BLOCK")
    assert bm.measure([wallet(4, "risky", listed, itself)]).rows[0].gaps == []
    frozen = replace(CLEAN, rules=("R-FRZ-01",), verdict="BLOCK")
    illicit = "Illicit Activity/direct/40"
    assert bm.measure([wallet(5, "risky", frozen, illicit)]).rows[0].gaps == []


def test_at70_passes_at_80_percent_with_every_difference_explained() -> None:
    same = [wallet(n, "risky") for n in range(8)]
    off = [wallet(n, "low") for n in (8, 9)]
    assert bm.measure(same + off).share == Decimal("0.8")
    assert not bm.measure(same + off).passed  # two mismatches without a reason
    why = [replace(w, cause="disputed", reason="tiny exposure") for w in off]
    assert bm.measure(same + why).passed
    assert not bm.measure(same[1:] + why).passed  # 7 of 9: 78%
    gapped = wallet(0, "risky", REVIEW, "Risky Exchange/direct/9")
    assert not bm.measure([gapped, *same[1:], *why]).passed  # the same level, but a gap
    explained = replace(gapped, cause="unknowable", reason="a private label")
    assert bm.measure([explained, *same[1:], *why]).passed


def test_a_lower_bound_is_marked() -> None:
    row = bm.measure([wallet(1, "risky", replace(REVIEW, verdict="INCOMPLETE"))]).rows[0]
    assert row.lower_bound
    assert "≥ 47 · moderate (INCOMPLETE)" in bm.markdown(bm.measure([row.wallet]))


# D-092: a candidate is adopted only if it fixes at least 2 mismatches and breaks none.
def test_a_candidate_is_adopted_for_patterns_only() -> None:
    lows = [wallet(1, "low"), wallet(2, "low")]  # MistTrack: low; ours: moderate
    keeper = wallet(3, "risky")
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


def test_read_lookups(tmp_path: Path) -> None:
    f = tmp_path / "lookups.csv"
    f.write_text(
        "Address,Chain,Level,Risks,Volume,Note\n"
        f'{TRON},tron,Risky,"Illicit Activity/direct/52.07; Sanctioned Entity/indirect/15.91",'
        "$343958,our counterparty\n"
        f"{BSC},,low,,,\n",
        encoding="utf-8",
    )
    wallets, ignored = bm.read_lookups(f, "2026-10-07")
    assert ignored == ["volume"]
    assert [(w.chain, w.address, w.theirs, len(w.risks), w.note) for w in wallets] == [
        ("tron", TRON, "risky", 2, "our counterparty"),
        ("bsc", BSC, "low", 0, None),
    ]
    assert wallets[0].source == "MistTrack Light, 2026-10-07"
    f.write_text(f"address,level\n{TRON},moderate\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"line 2: .*level must be one of low, risky"):
        bm.read_lookups(f, "2026-10-07")
    f.write_text(f"address,level,risks\n{TRON},risky,Scam/direct/5\n", encoding="utf-8")
    with pytest.raises(ValueError, match="line 2: unknown MistTrack risk type"):
        bm.read_lookups(f, "2026-10-07")
    f.write_text(f"wallet,level\n{TRON},low\n", encoding="utf-8")
    with pytest.raises(ValueError, match="needs the columns address and level"):
        bm.read_lookups(f, "2026-10-07")


def test_the_set_round_trips_and_merges(tmp_path: Path) -> None:
    path = tmp_path / "b.json"
    rows = "Sanctioned Entity/indirect/14.86"
    first = [wallet(1, "risky", REVIEW, rows, cause="our_miss", reason="x"), wallet(2, "low", None)]
    bm.dump(first, path)
    assert bm.load(path) == first
    again = bm.merge(bm.load(path), [wallet(1, "risky", None, rows)])  # the same lookup again
    one = next(w for w in again if w.address.endswith("1"))
    assert (one.cause, one.reason, one.recorded) == ("our_miss", "x", REVIEW)
    changed = wallet(1, "risky", None, "Sanctioned Entity/indirect/20")  # a different table
    merged = bm.merge(bm.load(path), [changed, wallet(3, "low", None)])
    one = next(w for w in merged if w.address.endswith("1"))
    assert (one.cause, one.reason, one.recorded) == (None, None, REVIEW)
    assert len(merged) == 3


def test_the_report() -> None:
    wallets = [
        wallet(1, "risky", REVIEW, "Sanctioned Entity/indirect/14.86"),
        wallet(2, "low"),
        wallet(3, "risky", BLOCK, "Risky Exchange/direct/7", cause="unknowable", reason="label"),
    ]
    text = bm.markdown(bm.measure(wallets), bm.measure(wallets, k=Decimal(4)))
    assert "3 wallet(s): same level 2 (67%), 1 mismatched, 1 with gaps." in text
    assert "**AT-70: not passed**" in text
    assert "| low | 0 | 1 | 0 | 0 |" in text  # the levels table
    assert "| risky | 0 | 1 | 0 | 1 |" in text
    assert "Sanctioned Entity indirect 14.86%" in text
    assert "**to explain**" in text  # wallet 2 disagrees and has no reason yet
    assert "unknowable: label" in text
    assert "## Candidate: k = 4" in text
    assert "Adoptable under D-092: no" in text  # k = 4 fixes wallet 2 but breaks wallet 1
    assert bm.markdown(bm.measure([])).endswith("No wallet recorded yet (Q-37).\n")


def test_the_recorded_set_reproduces() -> None:
    """Every record in the set gives back its score with its own settings (§14.3)."""
    assert bm.unreproduced(bm.load(SET)) == []
