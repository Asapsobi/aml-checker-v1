"""Score version 2 (methodology §11.3–§11.4, D-071), and v1 scores read back as stored."""

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from amlcheck.config import Score as ScoreSettings
from amlcheck.core.models import Finding, Severity, Verdict
from amlcheck.core.risk import Exposure
from amlcheck.core.score import Score, ScoreV1, compute, from_json, hazards, level, shown

D0 = Decimal
NOW = datetime(2026, 10, 3, tzinfo=UTC)
S = ScoreSettings()


def f(rule: str, n: int = 0) -> Finding:
    return Finding(rule, Severity.INFO, "x", f"{rule} #{n}", NOW)


def e(
    percent: str,
    category: str = "sanctioned",
    *,
    hop: int = 1,
    direction: str = "in",
    confidence: str | None = None,
) -> Exposure:
    return Exposure(
        direction,
        hop,
        f"T{category}{hop}{direction}",
        category,
        "x",
        D0(1),
        D0(percent),
        (),
        D0(confidence) if confidence else None,
    )


def score(*exposures: Exposure, findings: tuple[str, ...] = ()) -> Score:
    return compute(Verdict.REVIEW, [f(r) for r in findings], exposures, S)


# AT-62: X just below and above 30.5, 70.5 and 90.5 → the four levels' edges.
@pytest.mark.parametrize(
    ("percent", "x", "points", "expected"),
    [
        ("0.0453", "30.4", 30, "low"),
        ("0.0454", "30.5", 31, "moderate"),
        ("0.152", "70.4", 70, "moderate"),
        ("0.1525", "70.5", 71, "high"),
        ("0.2935", "90.4", 90, "high"),
        ("0.294", "90.5", 91, "severe"),
    ],
)
def test_at62_level_edges(percent: str, x: str, points: int, expected: str) -> None:
    s = score(e(percent))
    assert (s.exposure, s.score, s.level) == (D0(x), points, expected)


def test_at62_block_and_incomplete() -> None:
    block = compute(Verdict.BLOCK, [f("R-SAN-01")], [], S)
    assert (block.score, block.level, block.shown) == (100, "severe", "100 · severe")
    gap = compute(Verdict.INCOMPLETE, [f("R-SYS-01")], [e("0.05")], S)
    assert (gap.score, gap.lower_bound, gap.shown) == (33, True, "≥ 33 · moderate+")


@pytest.mark.parametrize(
    ("points", "expected"),
    [
        (0, "low"),
        (30, "low"),
        (31, "moderate"),
        (70, "moderate"),
        (71, "high"),
        (90, "high"),
        (91, "severe"),
        (100, "severe"),
    ],
)
def test_levels(points: int, expected: str) -> None:
    assert level(points) == expected


# Methodology §11.3 "feel" table, one exposure each.
@pytest.mark.parametrize(
    ("exposure", "x", "expected"),
    [
        (e("0.01", "scam"), "5.4", "low"),
        (e("0.02"), "14.8", "low"),
        (e("0.05", "mixer"), "27.4", "low"),
        (e("0.05"), "33.0", "moderate"),
        (e("0.1"), "55.1", "moderate"),
        (e("0.1", hop=2), "38.1", "moderate"),
        (e("0.1", hop=3), "25.0", "low"),
        (e("0.2"), "79.8", "high"),
        (e("0.3"), "90.9", "severe"),
    ],
)
def test_feel_table(exposure: Exposure, x: str, expected: str) -> None:
    s = score(exposure)
    assert (s.exposure, s.level) == (D0(x), expected)


def test_directions_combine_and_clamp() -> None:
    h = hazards(
        [e("0.1"), e("0.2", "frozen", direction="out"), e("0.05", direction="out")], S.decay
    )
    assert h == {"in": D0("0.1"), "out": D0("0.23")}
    s = score(e("0.1"), e("0.2", "frozen", direction="out"), e("0.05", direction="out"))
    # H = 1 − 0.9 × 0.77 = 0.307 → X = 100 × (1 − e^(−2.456)) = 91.4
    assert (s.exposure, s.score) == (D0("91.4"), 91)
    assert hazards([e("0.8"), e("0.9", direction="in", hop=1, category="frozen")], S.decay) == {
        "in": D0(1),
        "out": D0(0),
    }  # each direction at most 1


def test_decay_off_and_inferred_confidence() -> None:
    far = e("0.1", hop=3)
    assert compute(Verdict.REVIEW, [], [far], ScoreSettings(decay=D0(0))).exposure == D0("55.1")
    inferred = e("0.2", "suspicious_collector", confidence="0.5")
    assert hazards([inferred], S.decay)["in"] == D0("0.05")  # 0.5 × 0.2 × 0.5


def test_behaviour_on_the_headroom_and_capped() -> None:
    s = score(e("0.05"), findings=("R-HEU-02", "R-HEU-01"))  # X 33.0, B 15
    assert (s.exposure, s.behaviour) == (D0("33.0"), D0(15))
    assert s.score == 43  # ⌊33 + 67 × 0.15 + 0.5⌋ = ⌊43.55⌋
    alone = score(findings=("R-HEU-02", "R-HEU-03", "R-HEU-06", "R-FRZ-02"))
    assert (alone.behaviour, alone.score, alone.level) == (D0(30), 30, "low")  # behaviour alone
    assert score(findings=("R-HEU-05",)).behaviour == 0  # its counterparties are exposures now


def test_only_block_reaches_100() -> None:
    s = score(e("1"), e("1", direction="out"), findings=("R-HEU-06", "R-FRZ-02"))
    assert s.score == 99


def test_a_rule_counts_once() -> None:
    s = compute(Verdict.REVIEW, [f("R-HEU-01", i) for i in range(5)], [], S)
    assert (s.behaviour, s.score) == (D0(5), 5)


def test_unknown_services_add_nothing() -> None:
    s = score()
    assert (s.score, s.level, s.exposure) == (0, "low", D0(0))


def test_json_round_trip_and_stored_breakdown() -> None:
    # H = 1 − 0.9 × 0.98 = 0.118 → X = 61.1; ⌊61.1 + 38.9 × 0.2 + 0.5⌋ = 69
    s = compute(Verdict.INCOMPLETE, [f("R-HEU-06")], [e("0.1"), e("0.02", direction="out")], S)
    assert from_json(s.dumps()) == s
    assert s.dumps() == (
        '{"components":{"B":"20","X":"61.1"},"decay":"0.4","hazard":{"in":"0.1","out":"0.02"},'
        '"k":"8","level":"moderate","lower_bound":true,"score":69,"score_version":2}'
    )
    # The stored X and B reproduce the score.
    total = s.exposure + (100 - s.exposure) * s.behaviour / 100 + D0("0.5")
    assert s.score == int(total // 1)
    assert s.breakdown == "exposure 61.1 · behaviour 20"


V1 = (
    '{"band":"high","components":{"B":"5","D":"0","E":"59.5","U":"1"},"hazard":"0.24",'
    '"lower_bound":false,"score":66,"score_version":1}'
)


def test_v1_scores_read_back_as_stored() -> None:
    s = from_json(V1)
    assert isinstance(s, ScoreV1)
    assert (s.score, s.band, s.level, s.version) == (66, "high", "high", 1)
    assert s.shown == "66 · high"
    assert s.breakdown == "E 59.5 · D 0 · B 5 · U 1"
    assert s.to_json() == json.loads(V1)


def test_shown_by_version() -> None:
    assert shown(25, "REVIEW") == "25 · low"  # v2
    assert shown(25, "REVIEW", 1) == "25 · medium"  # a v1 score keeps its v1 band
    assert shown(25, "REVIEW", None) == "25 · medium"  # version not recorded: before P12
    assert shown(None, "NO_HITS") == "-"
    assert shown(40, "INCOMPLETE") == "≥ 40 · moderate+"
