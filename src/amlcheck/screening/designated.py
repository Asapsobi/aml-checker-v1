"""Who the checked address itself is, when that is a risk (methodology §13.4–13.6, D-100–D-102).

- A designated entity's wallet: the operator (or a pack) labelled it `sanctioned_entity`, a
  public incident's index holds it (inferred), or its public explorer tag names an entity a
  sanctions list designates (HTX, Nobitex…): R-SAN-02.
- Any other risk label on it, by the operator or a pack (`scam`, `stolen_funds`, `mixer`…):
  R-LBL-01.

Each is REVIEW by default (the attribution is the operator's, a pack's or the explorer's, not a
list's; `[rules] severity` may raise them to BLOCK) and makes the address a direct exposure of its
category at 100%, as MistTrack reports one. Labels are local and decide first; the tag is looked up
only when no label or index names a designated entity. Not required: a failed lookup leaves the
check decidable and says so.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from amlcheck.core.clock import Clock, utcnow
from amlcheck.core.models import Address, Finding, SourceResult, SourceStatus
from amlcheck.core.risk import RISK, Exposure
from amlcheck.core.rules import finding
from amlcheck.intel.categories import BY_NAME
from amlcheck.intel.designations import designation_for
from amlcheck.intel.incidents import incident_for
from amlcheck.intel.names import designated_label
from amlcheck.intel.store import IntelStore
from amlcheck.intel.tags import TagCache
from amlcheck.screening.base import SourceHealth

SOURCE = "designated"
LABEL = "Who it is (labels, explorer tag)"

#: §13.6: categories that a list or our own inference decides, not a label on the address itself.
_NOT_OWN = frozenset(
    {"sanctioned", "frozen", "sanctioned_entity", "suspected_malicious", "suspicious_collector"}
)


@dataclass(frozen=True)
class _Hit:
    rule: str
    category: str
    entity: str
    summary: str
    evidence: dict[str, Any]
    confidence: Decimal | None = None


class DesignatedSource:
    source = SOURCE
    label = LABEL
    required = False
    timeout: float | None = None

    def __init__(
        self, conn: sqlite3.Connection, tags: TagCache | None, *, clock: Clock = utcnow
    ) -> None:
        self._conn = conn
        self._tags = tags
        self._clock = clock

    async def check(self, address: Address) -> SourceResult:
        now = self._clock()
        hits: list[_Hit] = []
        designated, failed, detail = await self._designated(address)
        if designated is not None:
            hits.append(designated)
        own = self._own_label(address)
        if own is not None:
            hits.append(own)
        if not hits:
            return SourceResult(
                self.source,
                self.label,
                False,
                SourceStatus.ERROR if failed else SourceStatus.OK,
                now,
                detail=detail,
            )
        findings: list[Finding] = []
        exposures: list[dict[str, Any]] = []
        for h in hits:
            findings.append(finding(h.rule, SOURCE, h.summary, now, h.evidence))
            exposure = Exposure(
                "in", 1, address.norm, h.category, h.entity, Decimal(0), Decimal(1),
                (address.norm,), h.confidence,
            )  # fmt: skip
            exposures.append(exposure.to_json())
        return SourceResult(
            self.source,
            self.label,
            False,
            SourceStatus.OK,
            now,
            tuple(findings),
            "; ".join(h.entity for h in hits),
            {"exposures": exposures},
        )

    async def _designated(self, address: Address) -> tuple[_Hit | None, bool, str]:
        """§13.4–13.5: a designated entity's wallet. Returns (hit, lookup failed, detail)."""
        label = designated_label(self._conn, address.chain, address.norm)
        if label is not None:
            entity, provenance, label_id = label
            return (
                _Hit(
                    "R-SAN-02",
                    "sanctioned_entity",
                    entity,
                    f"Labelled a designated entity's wallet by the {provenance}: {entity}",
                    {"entity": entity, "label_id": label_id, "provenance": provenance},
                ),
                False,
                "",
            )
        incident = incident_for(self._conn, address.chain, address.norm)
        if incident is not None:  # §13.5 (D-101): local, inferred
            d = incident.designation
            return (
                _Hit(
                    "R-SAN-02",
                    "sanctioned_entity",
                    d.text,
                    f"A wallet of {d.entity} (inferred, {incident.confidence}): {incident.what}; "
                    f"{', '.join(d.basis)} designates {d.entity}",
                    {
                        "entity": d.entity,
                        "basis": list(d.basis),
                        "incident": incident.id,
                        "confidence": str(incident.confidence),
                    },
                    incident.confidence,
                ),
                False,
                "",
            )
        if self._tags is None:
            return None, False, "no label; no tags"
        failures = self._tags.failures
        tag = await self._tags.public_tag(address.chain, address.norm)
        if tag is None:
            failed = self._tags.failures > failures
            return None, failed, "the explorer tag lookup failed" if failed else "no tags here"
        found = designation_for(tag)
        if found is None:
            return None, False, f"{f'tag {tag!r}' if tag else 'no public tag'}: not designated"
        return (
            _Hit(
                "R-SAN-02",
                "sanctioned_entity",
                found.text,
                f"Tronscan tags this address {tag!r}: a wallet of {found.entity}, which "
                f"{', '.join(found.basis)} designates",
                {
                    "entity": found.entity,
                    "basis": list(found.basis),
                    "tag": tag,
                    **self._tags.provenance(address.chain, address.norm),
                },
            ),
            False,
            "",
        )

    def _own_label(self, address: Address) -> _Hit | None:
        """§13.6 (D-102): the most severe risk label the operator or a pack put on the address
        itself (§8 order; then the oldest). Inferred labels reach the score through the trace."""
        labels = sorted(
            (BY_NAME[x.category].order, x.id, x)
            for x in IntelStore(self._conn).labels(address.chain, address.norm)
            if x.provenance != "inferred" and x.category in RISK and x.category not in _NOT_OWN
        )
        if not labels:
            return None
        x = labels[0][2]
        entity = x.note or f"labelled {x.category}"
        return _Hit(
            "R-LBL-01",
            x.category,
            entity,
            f"Labelled {x.category} by the {x.provenance} ({x.source_ref}): {entity}",
            {
                "category": x.category,
                "label_id": x.id,
                "provenance": x.provenance,
                "source_ref": x.source_ref,
                "licence": x.licence,
            },
        )

    async def health(self) -> SourceHealth:
        return SourceHealth(
            self.source, self.label, SourceStatus.OK, None, "local labels; explorer tag, cached"
        )
