"""Is the checked address itself a designated entity's wallet (methodology §13.4, D-100)?

The operator (or a pack) labelled it `sanctioned_entity`, or its public explorer tag names an entity
a sanctions list designates (HTX, Nobitex…): R-SAN-02, REVIEW by default (the attribution is the
operator's or the explorer's, not the list's; `[rules] severity` may raise it to BLOCK), and the
address is a direct exposure to that entity at 100%, as MistTrack reports one. A label is local and
decides first; without one, the tag is looked up. Not required: a failed lookup leaves the check
decidable and says so.
"""

from __future__ import annotations

import sqlite3
from decimal import Decimal

from amlcheck.core.clock import Clock, utcnow
from amlcheck.core.models import Address, SourceResult, SourceStatus
from amlcheck.core.risk import Exposure
from amlcheck.core.rules import finding
from amlcheck.intel.designations import designation_for
from amlcheck.intel.incidents import incident_for
from amlcheck.intel.names import designated_label
from amlcheck.intel.tags import TagCache
from amlcheck.screening.base import SourceHealth

SOURCE = "designated"
LABEL = "Designated entities (labels, explorer tag)"


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

    def _result(
        self,
        address: Address,
        entity: str,
        summary: str,
        evidence: dict[str, object],
        confidence: Decimal | None = None,
    ) -> SourceResult:
        now = self._clock()
        exposure = Exposure(
            "in", 1, address.norm, "sanctioned_entity", entity, Decimal(0), Decimal(1),
            (address.norm,), confidence,
        )  # fmt: skip
        return SourceResult(
            self.source,
            self.label,
            False,
            SourceStatus.OK,
            now,
            (finding("R-SAN-02", SOURCE, summary, now, evidence),),
            entity,
            {"exposures": [exposure.to_json()]},
        )

    async def check(self, address: Address) -> SourceResult:
        now = self._clock()
        label = designated_label(self._conn, address.chain, address.norm)
        if label is not None:
            entity, provenance, label_id = label
            return self._result(
                address,
                entity,
                f"Labelled a designated entity's wallet by the {provenance}: {entity}",
                {"entity": entity, "label_id": label_id, "provenance": provenance},
            )
        incident = incident_for(self._conn, address.chain, address.norm)
        if incident is not None:  # §13.5 (D-101): local, inferred
            d = incident.designation
            return self._result(
                address,
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
            )
        if self._tags is None:
            return SourceResult(
                self.source, self.label, False, SourceStatus.OK, now, detail="no label; no tags"
            )
        failures = self._tags.failures
        tag = await self._tags.public_tag(address.chain, address.norm)
        if tag is None:
            failed = self._tags.failures > failures
            return SourceResult(
                self.source,
                self.label,
                False,
                SourceStatus.ERROR if failed else SourceStatus.OK,
                now,
                detail="the explorer tag lookup failed" if failed else "no tags for this chain",
            )
        found = designation_for(tag)
        if found is None:
            shown = f"tag {tag!r}" if tag else "no public tag"
            return SourceResult(
                self.source,
                self.label,
                False,
                SourceStatus.OK,
                now,
                detail=f"{shown}: not designated",
            )
        return self._result(
            address,
            found.text,
            f"Tronscan tags this address {tag!r}: a wallet of {found.entity}, which "
            f"{', '.join(found.basis)} designates",
            {
                "entity": found.entity,
                "basis": list(found.basis),
                "tag": tag,
                **self._tags.provenance(address.chain, address.norm),
            },
        )

    async def health(self) -> SourceHealth:
        return SourceHealth(
            self.source, self.label, SourceStatus.OK, None, "local labels; explorer tag, cached"
        )
