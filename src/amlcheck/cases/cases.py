"""Cases: open, decide, confirm or reject inferences (PRD F12.1, F12.3; D-058 to D-060).

- A case opens from a REVIEW, BLOCK or INCOMPLETE check of an address (its latest by default). An
  address has at most one open case: opening again returns it.
- A decision (`approved`, `rejected`, `escalated`) with a note and the operator's name goes into the
  decision chain, linked to the check it was made on and that check's record hash. `approved` and
  `rejected` close the case; `escalated` keeps it open.
- Inferences (methodology §6) can be confirmed or rejected inside an open or closed case
  (D-059): DEPOSIT → the entity membership becomes the operator's; HUB → its entity is named;
  COLLECTOR, DISTRIBUTOR, PASS_THROUGH → an operator label in a chosen category. A rejection
  suppresses the type for the address until the classifier version changes.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime

from amlcheck.cases import decisions
from amlcheck.cases.decisions import CLOSING, KINDS, Decision, Stored
from amlcheck.core.clock import to_db
from amlcheck.core.models import Address, Chain, Verdict
from amlcheck.intel.store import IntelError, IntelStore, NewLabel
from amlcheck.profile import classifier as clf
from amlcheck.storage.db import transaction

OPENABLE = frozenset({Verdict.REVIEW.value, Verdict.BLOCK.value, Verdict.INCOMPLETE.value})
LABEL_TYPES = frozenset({"COLLECTOR", "DISTRIBUTOR", "PASS_THROUGH"})


class CaseError(ValueError):
    """A request the case store refuses; the message is meant for the operator."""


@dataclass(frozen=True)
class Case:
    case_id: str
    chain: Chain
    address: str
    opened_from: str
    opened_by: str
    opened_at: str
    status: str
    client: str | None


@dataclass(frozen=True)
class Feedback:
    id: int
    case_id: str
    type: str
    classifier_version: int
    verdict: str
    label_id: int | None
    operator: str
    created_at: str


def need_name(name: str | None) -> str:
    """D-058: no anonymous decisions."""
    if not name or not name.strip():
        raise CaseError("who is deciding? set [operator] name in config.toml, or pass --by NAME")
    return name.strip()


_CASE_COLS = "case_id, chain, address_norm, opened_from, opened_by, opened_at, status, client"


def _case(r: tuple[object, ...]) -> Case:
    return Case(
        str(r[0]),
        Chain(str(r[1])),
        str(r[2]),
        str(r[3]),
        str(r[4]),
        str(r[5]),
        str(r[6]),
        str(r[7]) if r[7] is not None else None,
    )


def get(conn: sqlite3.Connection, case_id: str) -> Case | None:
    r = conn.execute(
        f"SELECT {_CASE_COLS} FROM cases WHERE case_id = ?",  # noqa: S608 - constant columns
        (case_id,),
    ).fetchone()
    return _case(r) if r else None


def find(conn: sqlite3.Connection, case_id: str) -> Case:
    """By full id or a unique prefix of at least 6 characters."""
    found = get(conn, case_id)
    if found:
        return found
    if len(case_id) >= 6:
        rows = conn.execute(
            f"SELECT {_CASE_COLS} FROM cases WHERE case_id LIKE ? || '%'",  # noqa: S608
            (case_id,),
        ).fetchall()
        if len(rows) == 1:
            return _case(rows[0])
    raise CaseError(f"no case {case_id}")


def cases(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    address: Address | None = None,
) -> list[Case]:
    rows = conn.execute(
        f"SELECT {_CASE_COLS} FROM cases "  # noqa: S608 - constant columns
        "WHERE (?1 IS NULL OR status = ?1) AND (?2 IS NULL OR (chain = ?2 AND address_norm = ?3)) "
        "ORDER BY opened_at DESC, case_id",
        (status, address.chain.value if address else None, address.norm if address else None),
    ).fetchall()
    return [_case(r) for r in rows]


def _check_row(conn: sqlite3.Connection, check_id: str) -> tuple[str, str, str, str]:
    """(chain, address, verdict, record_hash) of a check."""
    r = conn.execute(
        "SELECT chain, address_norm, verdict, record_hash FROM checks WHERE check_id = ?",
        (check_id,),
    ).fetchone()
    if r is None:
        raise CaseError(f"no check {check_id}")
    return str(r[0]), str(r[1]), str(r[2]), str(r[3])


def open_case(
    conn: sqlite3.Connection,
    address: Address,
    *,
    by: str | None,
    now: datetime,
    check_id: str | None = None,
) -> tuple[Case, bool]:
    """The open case for the address (created when there is none) and whether it is new."""
    name = need_name(by)
    existing = cases(conn, status="open", address=address)
    if existing:
        return existing[0], False
    if check_id is None:
        r = conn.execute(
            "SELECT check_id FROM checks WHERE chain = ? AND address_norm = ? "
            "ORDER BY seq DESC LIMIT 1",
            (address.chain.value, address.norm),
        ).fetchone()
        if r is None:
            raise CaseError(f"{address.norm} has never been checked; run `amlcheck check` first")
        check_id = str(r[0])
    chain, norm, verdict, _ = _check_row(conn, check_id)
    if (chain, norm) != (address.chain.value, address.norm):
        raise CaseError(f"check {check_id} is for {norm}, not {address.norm}")
    if verdict not in OPENABLE:
        raise CaseError(f"check {check_id} is {verdict}: cases are for REVIEW, BLOCK or INCOMPLETE")
    client = conn.execute("SELECT client FROM checks WHERE check_id = ?", (check_id,)).fetchone()
    case = Case(
        str(uuid.uuid4()),
        address.chain,
        address.norm,
        check_id,
        name,
        to_db(now),
        "open",
        client[0] if client else None,
    )
    with transaction(conn):
        conn.execute(
            f"INSERT INTO cases ({_CASE_COLS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",  # noqa: S608
            (
                case.case_id,
                case.chain.value,
                case.address,
                case.opened_from,
                case.opened_by,
                case.opened_at,
                case.status,
                case.client,
            ),
        )
    return case, True


def decide(
    conn: sqlite3.Connection,
    case: Case,
    decision: str,
    note: str,
    *,
    by: str | None,
    now: datetime,
    tool_version: str,
    check_id: str | None = None,
) -> Stored:
    """Record a decision in the chain; `approved` / `rejected` close the case (D-060)."""
    name = need_name(by)
    if decision not in KINDS:
        raise CaseError(f"a decision is one of {', '.join(KINDS)}, not {decision!r}")
    if not note.strip():
        raise CaseError("a decision needs a note: what was looked at and why")
    current = get(conn, case.case_id)
    if current is None or current.status != "open":
        raise CaseError(f"case {case.case_id} is closed; open a new case for a new question")
    on = check_id or case.opened_from
    chain, norm, _, record_hash = _check_row(conn, on)
    if (chain, norm) != (case.chain.value, case.address):
        raise CaseError(f"check {on} is for {norm}, not this case's address")
    stored = decisions.append(
        conn,
        Decision(
            decision_id=str(uuid.uuid4()),
            case_id=case.case_id,
            check_id=on,
            check_record_hash=record_hash,
            decision=decision,
            note=note.strip(),
            operator=name,
            created_at=to_db(now),
            tool_version=tool_version,
        ),
    )
    if decision in CLOSING:
        with transaction(conn):
            conn.execute("UPDATE cases SET status = 'closed' WHERE case_id = ?", (case.case_id,))
    return stored


# --- inferences (D-059) ---------------------------------------------------------------------------


def latest_types(conn: sqlite3.Connection, chain: Chain, address: str) -> list[str]:
    """Types in the address's latest stored classification of this classifier version, without
    the ones the operator has rejected since (a reclassification that finds nothing stores
    nothing, so the stored one can be older than the rejection)."""
    r = conn.execute(
        "SELECT max(computed_at) FROM classifications WHERE chain = ? AND address_norm = ? "
        "AND classifier_version = ?",
        (chain.value, address, clf.CLASSIFIER_VERSION),
    ).fetchone()
    if r is None or r[0] is None:
        return []
    gone = clf.suppressed(conn, chain, address)
    return [
        str(t)
        for (t,) in conn.execute(
            "SELECT DISTINCT type FROM classifications WHERE chain = ? AND address_norm = ? "
            "AND computed_at = ? AND classifier_version = ? ORDER BY id",
            (chain.value, address, r[0], clf.CLASSIFIER_VERSION),
        )
        if t not in gone
    ]


def _feedback(
    conn: sqlite3.Connection,
    case: Case,
    type_: str,
    verdict: str,
    label_id: int | None,
    name: str,
    now: datetime,
) -> int:
    with transaction(conn):
        cur = conn.execute(
            "INSERT INTO inference_feedback (case_id, chain, address_norm, type, "
            "classifier_version, verdict, label_id, operator, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                case.case_id,
                case.chain.value,
                case.address,
                type_,
                clf.CLASSIFIER_VERSION,
                verdict,
                label_id,
                name,
                to_db(now),
            ),
        )
    if cur.lastrowid is None:  # pragma: no cover
        raise CaseError("feedback not stored")
    return cur.lastrowid


def _classified_as(conn: sqlite3.Connection, case: Case, type_: str) -> str:
    t = type_.upper()
    types = latest_types(conn, case.chain, case.address)
    if t not in types:
        have = ", ".join(types) or "nothing"
        raise CaseError(f"{case.address} is not classified {t} (its latest types: {have})")
    return t


def confirm(
    conn: sqlite3.Connection,
    store: IntelStore,
    case: Case,
    type_: str,
    *,
    by: str | None,
    now: datetime,
    category: str | None = None,
    entity_name: str | None = None,
    kind: str | None = None,
) -> int:
    """Confirm an inferred type; returns the feedback id (D-059)."""
    name = need_name(by)
    t = _classified_as(conn, case, type_)
    label_id: int | None = None
    try:
        if t == "DEPOSIT":
            m = store.membership(case.chain, case.address)
            if m is None:
                raise CaseError("this deposit is not linked to an entity; classify it again first")
            entity_id, role, _, evidence = m
            store.link(
                case.chain,
                case.address,
                entity_id,
                role,
                {**evidence, "confirmed_in": case.case_id},
                provenance="operator",
            )
            if entity_name:
                store.name_entity(entity_id, entity_name, kind or "unknown", name)
        elif t == "HUB":
            if not entity_name or not kind:
                raise CaseError("confirming a HUB names its entity: give --name and --kind")
            m = store.membership(case.chain, case.address)
            if m is None:
                entity_id = store.create_entity(case.chain, entity_name, kind, named_by=name)
                store.link(
                    case.chain,
                    case.address,
                    entity_id,
                    "hub",
                    {"confirmed_in": case.case_id},
                    provenance="operator",
                )
            else:
                store.name_entity(m[0], entity_name, kind, name)
        elif t in LABEL_TYPES:
            if not category:
                raise CaseError(
                    f"confirming {t} labels the address: give --category (e.g. scam, high_risk)"
                )
            label_id = store.add_label(
                NewLabel(
                    case.chain,
                    case.address,
                    category,
                    "operator",
                    f"case:{case.case_id}",
                    note=f"confirmed {t}",
                    created_by=name,
                )
            )
    except IntelError as e:
        raise CaseError(str(e)) from None
    return _feedback(conn, case, t, "confirmed", label_id, name, now)


def reject(
    conn: sqlite3.Connection,
    store: IntelStore,
    case: Case,
    type_: str,
    *,
    by: str | None,
    now: datetime,
) -> int:
    """Reject an inferred type: suppressed for the address until the classifier version changes."""
    name = need_name(by)
    t = _classified_as(conn, case, type_)
    if t == "DEPOSIT":
        store.unlink_inferred(case.chain, case.address)  # not a deposit: not in the hub's entity
    return _feedback(conn, case, t, "rejected", None, name, now)


def feedback(conn: sqlite3.Connection, case_id: str) -> list[Feedback]:
    rows = conn.execute(
        "SELECT id, case_id, type, classifier_version, verdict, label_id, operator, created_at "
        "FROM inference_feedback WHERE case_id = ? ORDER BY id",
        (case_id,),
    ).fetchall()
    return [Feedback(*r) for r in rows]
