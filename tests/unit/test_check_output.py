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

History  2 transfer(s) since 2026-04-04 · received 1250 · sent 0 USDT · first activity 2026-09-28T00:00:00Z
         3 0-value transfer(s) dropped (address poisoning)

Counterparties  (largest 2 of 2)
  address                                     received              sent    txs  flags
  TFROZENxxxxxxxxxxxxxxxxxxxxxxxxxxx              1200                 0      1  frozen
  TOKxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx                50                 0      1  -

Check c-1 · 2026-10-01T12:00:00Z · audit 0123456789abcdef · amlcheck 0.3.0
Internal use only. NO_HITS means nothing was found in the sources checked, as of the times shown; it is not a clearance.
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
