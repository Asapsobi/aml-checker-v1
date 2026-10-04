from datetime import UTC, datetime

from amlcheck.cli.check import print_result
from amlcheck.core.address import detect
from amlcheck.core.models import CheckResult, Finding, Severity, SourceResult, SourceStatus, Verdict

NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)
ME = "TNHrhtVnRMRaTJFRsqLUqSytCKsavXJJaa"

EXPECTED = """\
REVIEW  ·  TRON TNHrhtVnRMRaTJFRsqLUqSytCKsavXJJaa
Review by hand before transacting.

Findings
  R-EXP-01  REVIEW     Dealt directly with a frozen address TFROZENxxxxxxxxxxxxxxxxxxxxxxxxxxx (received 1200, sent 0 USDT)
  R-HEU-01  REVIEW     New address: first activity 2026-09-28T00:00:00Z  (low priority)

Sources
  ok       OFAC SDN list                list of 2026-09-30, downloaded 1 h ago (1066 addresses)
  ok       USDT history (exposure)      2 transfer(s) with 2 counterparties since 2026-04-04

History  2 transfer(s) since 2026-04-04 · received 1,250.00 · sent 0.00 USDT · first activity 2026-09-28T00:00:00Z
         3 0-value transfer(s) dropped (address poisoning)

Counterparties  (largest 2 of 2)
  address                                   received            sent    txs  known as
  TFROZENxxxxxxxxxxxxxxxxxxxxxxxxxxx        1,200.00            0.00      1  frozen
  TOKxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx           50.00            0.00      1  -

Check c-1 · 2026-10-01T12:00:00Z · audit 0123456789abcdef · amlcheck 0.3.0
Internal use only. NO_HITS means no rule needs a review and the score is below the review threshold, in the sources checked, as of the times shown; it is not a clearance.
"""


def cp(address: str, received: str, flags: list[str]) -> dict[str, object]:
    return {
        "address": address,
        "received_usdt": received,
        "sent_usdt": "0",
        "transfers": 1,
        "flags": flags,
        "largest": [],
    }


def result() -> CheckResult:
    exposure = SourceResult(
        "exposure",
        "USDT history (exposure)",
        True,
        SourceStatus.OK,
        NOW,
        (),
        "2 transfer(s) with 2 counterparties since 2026-04-04",
        {
            "since": "2026-04-04T12:00:00Z",
            "transfers": 2,
            "received_usdt": "1250",
            "sent_usdt": "0",
            "first_activity": "2026-09-28T00:00:00Z",
            "zero_value_dropped": 3,
            "counterparty_count": 2,
            "counterparties": [
                cp("TFROZEN" + "x" * 27, "1200", ["frozen"]),
                cp("TOK" + "x" * 31, "50", []),
            ],
        },
    )
    ofac = SourceResult(
        "ofac_sdn",
        "OFAC SDN list",
        True,
        SourceStatus.OK,
        NOW,
        (),
        "list of 2026-09-30, downloaded 1 h ago (1066 addresses)",
    )
    findings = (
        Finding(
            "R-EXP-01",
            Severity.REVIEW,
            "exposure",
            f"Dealt directly with a frozen address {'TFROZEN' + 'x' * 27} (received 1200, sent 0 USDT)",
            NOW,
        ),
        Finding(
            "R-HEU-01",
            Severity.REVIEW,
            "exposure",
            "New address: first activity 2026-09-28T00:00:00Z",
            NOW,
            {"priority": "low"},
        ),
    )
    return CheckResult(
        detect(ME),
        Verdict.REVIEW,
        NOW,
        (ofac, exposure),
        findings,
        "0.3.0",
        "h",
        "c-1",
        "0123456789abcdef" + "0" * 48,
        amount=None,
    )


def test_human_output_snapshot(capsys) -> None:  # type: ignore[no-untyped-def]
    print_result(result())
    assert capsys.readouterr().out == EXPECTED


def test_score_line_snapshot(capsys) -> None:  # type: ignore[no-untyped-def]
    from dataclasses import replace

    from amlcheck.config import Score as ScoreSettings
    from amlcheck.core.score import compute

    base = result()
    scored = replace(base, score=compute(Verdict.REVIEW, base.findings, [], ScoreSettings()))
    print_result(scored)
    lines = capsys.readouterr().out.splitlines()
    assert lines[:3] == [
        "REVIEW  ·  TRON TNHrhtVnRMRaTJFRsqLUqSytCKsavXJJaa",
        "Review by hand before transacting.",
        "Score 5 · low  (exposure 0 · behaviour 5)",
    ]
    gap = replace(
        base,
        verdict=Verdict.INCOMPLETE,
        score=compute(Verdict.INCOMPLETE, base.findings, [], ScoreSettings()),
    )
    print_result(gap)
    assert capsys.readouterr().out.splitlines()[2] == (
        "Score ≥ 5 · low+  (exposure 0 · behaviour 5)  lower bound: a required source is missing"
    )


# P12: who the address is, the risk lines and the heaviest exposures, under the score.
async def test_v2_risk_lines(tmp_path, capsys) -> None:  # type: ignore[no-untyped-def]
    from amlcheck.config import Settings
    from amlcheck.core.clock import fixed
    from amlcheck.core.engine import screen
    from amlcheck.storage.db import open_db
    from tests.unit.test_engine import Fake
    from tests.unit.test_label import Classified
    from tests.unit.test_score_check import traced
    from tests.unit.trace_world import NOW as T_NOW
    from tests.unit.trace_world import T

    conn = open_db(tmp_path / "a.db")
    r = await screen(
        detect(T),
        [traced(conn), Fake("exposure", rules=("R-HEU-01",)), Classified("PERSONAL", "0.5")],
        conn=conn,
        settings=Settings(),
        now=fixed(T_NOW),
    )
    print_result(r)
    lines = capsys.readouterr().out.splitlines()
    assert lines[2:6] == [
        "Who    PERSONAL (inferred, 0.5)",
        "Score 70 · moderate  (exposure 68.4 · behaviour 5)",
        "       Sanctioned entity: indirect received 20.0%",
        "       Illicit activity: indirect received 10.0% (inferred)",
    ]
    table = lines[
        lines.index("Exposures  (heaviest 2 of 2; direct amounts exact, indirect ones estimated)") :
    ]
    assert table[2] == (
        "  in    2  sanctioned_entity    20.0%        4,000.00  OFAC SDN: Bad · 0x440000…000000"
    )
    assert table[3].endswith("COLLECTOR (inferred, 0.8) · 0x450000…000000")
