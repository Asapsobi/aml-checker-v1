"""Labels, entities, membership, best_terminal() (PRD F7.1, F7.2, architecture §3).

- Labels carry a category (methodology §8), provenance, source reference, confidence, licence,
  author and time. They are retracted, never deleted (F7.1, AT-31).
- An entity groups addresses believed to share one owner; an address is in at most one entity.
- `best_terminal()` is methodology §7.5 test 4: the address's decisive category from active labels,
  labels.csv tags that carry a category, and its entity's kind. Lists, operator and import labels
  rank first, then the highest confidence, then category order (§8).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from amlcheck.chain.base import canonical_amount
from amlcheck.core.clock import Clock, from_iso, to_db, utcnow
from amlcheck.core.models import Chain
from amlcheck.intel.categories import (
    BY_NAME,
    CATEGORY_VERSION,
    LABELS_CSV_CATEGORY,
    check_assignable,
    get,
)
from amlcheck.storage.db import transaction

_HUMAN_FIRST = {"list": 0, "operator": 0, "import": 0, "inferred": 1}


class IntelError(ValueError):
    """A request the store refuses. The message is meant for the operator."""


@dataclass(frozen=True)
class NewLabel:
    chain: Chain
    address_norm: str
    category: str
    provenance: str
    source_ref: str
    confidence: Decimal = Decimal(1)
    note: str | None = None
    licence: str | None = None
    created_by: str | None = None


@dataclass(frozen=True)
class LabelRow:
    id: int
    chain: Chain
    address_norm: str
    category: str
    provenance: str
    source_ref: str
    confidence: Decimal
    note: str | None
    licence: str | None
    category_version: int
    created_by: str | None
    created_at: datetime
    retracted_at: datetime | None
    retracted_by: str | None
    retract_reason: str | None

    @property
    def active(self) -> bool:
        return self.retracted_at is None


@dataclass(frozen=True)
class Entity:
    id: int
    chain: Chain
    name: str
    kind: str  # a category that may be an entity kind, or "unknown"
    named_by: str | None
    role: str | None = None  # the address's role, when looked up through a member


@dataclass(frozen=True)
class Terminal:
    category: str
    provenance: str
    confidence: Decimal
    source_ref: str
    label_id: int | None = None
    entity_id: int | None = None


class IntelStore:
    def __init__(self, conn: sqlite3.Connection, *, clock: Clock = utcnow) -> None:
        self._conn = conn
        self._clock = clock

    # --- labels -------------------------------------------------------------------------------

    def add_label(self, label: NewLabel) -> int:
        try:
            check_assignable(label.category, label.provenance)
        except ValueError as e:
            raise IntelError(str(e)) from None
        if label.provenance == "import" and not (label.licence or "").strip():
            raise IntelError("imported labels need a recorded licence (PRD F7.4)")
        if not Decimal(0) < label.confidence <= Decimal(1):
            raise IntelError("confidence must be above 0 and at most 1")
        if label.provenance != "inferred" and label.confidence != 1:
            raise IntelError("list, operator and import labels have confidence 1")
        with transaction(self._conn):
            cur = self._conn.execute(
                "INSERT INTO intel_labels (chain, address_norm, category, provenance, source_ref, "
                "confidence, note, licence, category_version, created_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    label.chain.value,
                    label.address_norm,
                    label.category,
                    label.provenance,
                    label.source_ref,
                    canonical_amount(label.confidence),
                    label.note,
                    label.licence,
                    CATEGORY_VERSION,
                    label.created_by,
                    to_db(self._clock()),
                ),
            )
        if cur.lastrowid is None:
            raise IntelError("label not stored")
        return cur.lastrowid

    def retract(self, label_id: int, reason: str, by: str | None) -> None:
        if not reason.strip():
            raise IntelError("a retraction needs a reason")
        row = self._conn.execute(
            "SELECT retracted_at FROM intel_labels WHERE id = ?", (label_id,)
        ).fetchone()
        if row is None:
            raise IntelError(f"no label #{label_id}")
        if row[0] is not None:
            raise IntelError(f"label #{label_id} is already retracted")
        with transaction(self._conn):
            self._conn.execute(
                "UPDATE intel_labels SET retracted_at = ?, retracted_by = ?, retract_reason = ? "
                "WHERE id = ? AND retracted_at IS NULL",
                (to_db(self._clock()), by, reason.strip(), label_id),
            )

    def labels(
        self,
        chain: Chain | None = None,
        address: str | None = None,
        *,
        include_retracted: bool = False,
    ) -> list[LabelRow]:
        rows = self._conn.execute(
            "SELECT id, chain, address_norm, category, provenance, source_ref, confidence, note, "
            "licence, category_version, created_by, created_at, retracted_at, retracted_by, "
            "retract_reason FROM intel_labels "
            "WHERE (?1 IS NULL OR chain = ?1) AND (?2 IS NULL OR address_norm = ?2) "
            "AND (?3 OR retracted_at IS NULL) ORDER BY chain, address_norm, id",
            (chain.value if chain else None, address, int(include_retracted)),
        ).fetchall()
        return [
            LabelRow(
                r[0],
                Chain(r[1]),
                r[2],
                r[3],
                r[4],
                r[5],
                Decimal(r[6]),
                r[7],
                r[8],
                r[9],
                r[10],
                from_iso(r[11]),
                from_iso(r[12]) if r[12] else None,
                r[13],
                r[14],
            )
            for r in rows
        ]

    def stats(self) -> dict[str, Any]:
        by: dict[str, dict[str, int]] = {}
        for prov, cat, n in self._conn.execute(
            "SELECT provenance, category, count(*) FROM intel_labels WHERE retracted_at IS NULL "
            "GROUP BY provenance, category ORDER BY provenance, category"
        ):
            by.setdefault(prov, {})[cat] = n
        retracted = self._conn.execute(
            "SELECT count(*) FROM intel_labels WHERE retracted_at IS NOT NULL"
        ).fetchone()[0]
        entities = self._conn.execute("SELECT count(*) FROM entities").fetchone()[0]
        members = self._conn.execute("SELECT count(*) FROM entity_members").fetchone()[0]
        csv = self._conn.execute("SELECT count(*) FROM labels").fetchone()[0]
        return {
            "active_labels": by,
            "retracted_labels": retracted,
            "entities": entities,
            "entity_members": members,
            "labels_csv_rows": csv,
        }

    # --- entities -----------------------------------------------------------------------------

    def create_entity(
        self, chain: Chain, name: str, kind: str = "unknown", named_by: str | None = None
    ) -> int:
        self._check_kind(kind)
        now = to_db(self._clock())
        with transaction(self._conn):
            cur = self._conn.execute(
                "INSERT INTO entities (chain, name, kind, named_by, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (chain.value, name, kind, named_by, now, now),
            )
        if cur.lastrowid is None:
            raise IntelError("entity not stored")
        return cur.lastrowid

    def name_entity(self, entity_id: int, name: str, kind: str, by: str | None) -> None:
        self._check_kind(kind)
        with transaction(self._conn):
            cur = self._conn.execute(
                "UPDATE entities SET name = ?, kind = ?, named_by = ?, updated_at = ? WHERE id = ?",
                (name, kind, by, to_db(self._clock()), entity_id),
            )
        if cur.rowcount == 0:
            raise IntelError(f"no entity #{entity_id}")

    def link(
        self,
        chain: Chain,
        address: str,
        entity_id: int,
        role: str,
        evidence: dict[str, Any],
        *,
        provenance: str = "inferred",
    ) -> None:
        """Put `address` in the entity, replacing any earlier membership (one per address)."""
        if role not in ("hub", "deposit", "member"):
            raise IntelError(f"role must be hub, deposit or member, not {role!r}")
        if provenance not in ("inferred", "operator"):
            raise IntelError("membership provenance is inferred or operator")
        if self.entity(entity_id) is None:
            raise IntelError(f"no entity #{entity_id}")
        with transaction(self._conn):
            self._conn.execute(
                "INSERT OR REPLACE INTO entity_members (entity_id, chain, address_norm, role, "
                "provenance, evidence_json, linked_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    entity_id,
                    chain.value,
                    address,
                    role,
                    provenance,
                    json.dumps(evidence, sort_keys=True, default=str),
                    to_db(self._clock()),
                ),
            )

    def membership(self, chain: Chain, address: str) -> tuple[int, str, str, dict[str, Any]] | None:
        """(entity id, role, provenance, evidence) of the address's membership, if any."""
        r = self._conn.execute(
            "SELECT entity_id, role, provenance, evidence_json FROM entity_members "
            "WHERE chain = ? AND address_norm = ?",
            (chain.value, address),
        ).fetchone()
        return (r[0], r[1], r[2], json.loads(r[3])) if r else None

    def unlink_inferred(self, chain: Chain, address: str) -> bool:
        """Drop an inferred membership (a rejected DEPOSIT, D-059); an operator's one stays."""
        with transaction(self._conn):
            cur = self._conn.execute(
                "DELETE FROM entity_members WHERE chain = ? AND address_norm = ? "
                "AND provenance = 'inferred'",
                (chain.value, address),
            )
        return cur.rowcount == 1

    def entity(self, entity_id: int) -> Entity | None:
        r = self._conn.execute(
            "SELECT id, chain, name, kind, named_by FROM entities WHERE id = ?", (entity_id,)
        ).fetchone()
        return Entity(r[0], Chain(r[1]), r[2], r[3], r[4]) if r else None

    def entities(self, chain: Chain | None = None) -> list[tuple[Entity, int]]:
        """Every entity with its member count, newest first."""
        rows = self._conn.execute(
            "SELECT e.id, e.chain, e.name, e.kind, e.named_by, count(m.address_norm) "
            "FROM entities e LEFT JOIN entity_members m ON m.entity_id = e.id "
            "WHERE (?1 IS NULL OR e.chain = ?1) "
            "GROUP BY e.id ORDER BY e.id DESC",
            (chain.value if chain else None,),
        ).fetchall()
        return [(Entity(r[0], Chain(r[1]), r[2], r[3], r[4]), r[5]) for r in rows]

    def members(self, entity_id: int) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT address_norm, role, provenance, evidence_json, linked_at FROM entity_members "
            "WHERE entity_id = ? ORDER BY role != 'hub', address_norm",
            (entity_id,),
        ).fetchall()
        return [
            {
                "address": r[0],
                "role": r[1],
                "provenance": r[2],
                "evidence": json.loads(r[3]),
                "linked_at": r[4],
            }
            for r in rows
        ]

    def entity_for(self, chain: Chain, address: str) -> Entity | None:
        r = self._conn.execute(
            "SELECT e.id, e.chain, e.name, e.kind, e.named_by, m.role FROM entity_members m "
            "JOIN entities e ON e.id = m.entity_id WHERE m.chain = ? AND m.address_norm = ?",
            (chain.value, address),
        ).fetchone()
        return Entity(r[0], Chain(r[1]), r[2], r[3], r[4], r[5]) if r else None

    @staticmethod
    def _check_kind(kind: str) -> None:
        if kind == "unknown":
            return
        try:
            category = get(kind)
        except ValueError as e:
            raise IntelError(str(e)) from None
        if not category.entity_kind:
            raise IntelError(f"{kind} can't be an entity's kind (categories 3-17 only)")

    # --- terminal -----------------------------------------------------------------------------

    def best_terminal(self, chain: Chain, address: str) -> Terminal | None:
        candidates: list[Terminal] = [
            Terminal(x.category, x.provenance, x.confidence, x.source_ref, label_id=x.id)
            for x in self.labels(chain, address)
        ]
        for (tag,) in self._conn.execute(
            "SELECT tag FROM labels WHERE chain = ? AND address_norm = ?", (chain.value, address)
        ):
            if tag in LABELS_CSV_CATEGORY:
                candidates.append(
                    Terminal(LABELS_CSV_CATEGORY[tag], "operator", Decimal(1), "labels.csv")
                )
        entity = self.entity_for(chain, address)
        if entity is not None and entity.kind != "unknown":
            provenance = "operator" if entity.named_by else "inferred"
            candidates.append(
                Terminal(
                    entity.kind, provenance, Decimal(1), f"entity:{entity.id}", entity_id=entity.id
                )
            )
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda t: (
                _HUMAN_FIRST[t.provenance],
                -t.confidence,
                BY_NAME[t.category].order,
                t.source_ref,
            ),
        )
