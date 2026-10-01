"""Rule IDs, default severities, overrides, R-SYS-01, fixed rules.

P0 holds only the rule catalogue, so config can refuse unknown IDs and forbidden overrides at load
(AT-02). The rule logic is built in P2.
"""

from __future__ import annotations

from amlcheck.core.models import Severity

#: PRD §7.2 defaults. R-SCR-01 is off unless `[score] review_at` > 0.
DEFAULT_SEVERITY: dict[str, Severity] = {
    "R-SAN-01": Severity.BLOCK,
    "R-FRZ-01": Severity.BLOCK,
    "R-FRZ-02": Severity.REVIEW,
    "R-SYS-01": Severity.INCOMPLETE,
    "R-EXP-01": Severity.REVIEW,
    "R-EXP-02": Severity.REVIEW,
    "R-HEU-01": Severity.REVIEW,
    "R-HEU-02": Severity.REVIEW,
    "R-HEU-03": Severity.REVIEW,
    "R-HEU-04": Severity.REVIEW,
    "R-HEU-05": Severity.REVIEW,
    "R-HEU-06": Severity.REVIEW,
    "R-HEU-07": Severity.REVIEW,
    "R-TRC-01": Severity.REVIEW,
    "R-TRC-02": Severity.REVIEW,
    "R-TRC-03": Severity.REVIEW,
    "R-TRC-04": Severity.REVIEW,
    "R-TRC-05": Severity.REVIEW,
    "R-SCR-01": Severity.REVIEW,
}

#: Severity can't be changed at all (PRD §7.2 "fixed").
FIXED: frozenset[str] = frozenset({"R-SYS-01"})

#: Inferences never block (methodology §2.5, D-017).
NEVER_BLOCK: frozenset[str] = frozenset({"R-HEU-07", "R-TRC-05"})
