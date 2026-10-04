"""Classifier, version 1 (methodology §6): who an address probably is, and why.

Pure `classify()`: the profile (§5) plus a little context about the top recipient → zero or more
types with confidence. A type needs every required condition; confidence is the base plus each bonus
that holds, capped at 1.0, and the type is assigned only at ≥ `min_confidence` (0.6), PERSONAL at
`personal_confidence` (0.5). The primary type is the first assigned in table order; FRESH is a tag
beside it. A missing feature (`None`: e.g. no outflow) never satisfies a condition.

Inferences never block (D-017) and an operator label wins over them (PRD F8.4).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from amlcheck.chain.base import canonical_amount
from amlcheck.config import Classifier
from amlcheck.core.clock import from_iso, to_db
from amlcheck.core.models import Chain
from amlcheck.profile.features import Profile
from amlcheck.storage.db import transaction

CLASSIFIER_VERSION = 1
TYPES = ("CONTRACT", "HUB", "DEPOSIT", "COLLECTOR", "DISTRIBUTOR", "PASS_THROUGH", "PERSONAL")
FRESH = "FRESH"

#: Terminal categories per type (methodology §6, §7.5).
TERMINAL = {
    "CONTRACT": "contract_unattributed",
    "HUB": "service_unattributed",
    "DEPOSIT": "service_unattributed",
    "COLLECTOR": "suspicious_collector",
}


@dataclass(frozen=True)
class ClassifyContext:
    """What `classify` needs to know beyond the address's own profile."""

    top_recipient_is_hub: bool = False  # top recipient classified HUB or labelled exchange/service
    hub_entity_named: bool = False  # that hub's entity was named by the operator
    top_recipient_fresh_or_pass_through: bool = False


@dataclass(frozen=True)
class Classification:
    type: str
    confidence: Decimal
    primary: bool
    conditions: tuple[str, ...]
    bonuses: tuple[str, ...] = field(default=())


def _le(x: Decimal | int | None, limit: Decimal | int) -> bool:
    return x is not None and x <= limit


def _ge(x: Decimal | int | None, limit: Decimal | int) -> bool:
    return x is not None and x >= limit


def _check(
    required: Sequence[tuple[str, bool]],
    base: str,
    bonuses: Sequence[tuple[str, bool, str]],
) -> tuple[Decimal, tuple[str, ...], tuple[str, ...]] | None:
    if not all(ok for _, ok in required):
        return None
    held = tuple(name for name, ok, _ in bonuses if ok)
    confidence = Decimal(base) + sum((Decimal(add) for _, ok, add in bonuses if ok), Decimal(0))
    return min(confidence, Decimal(1)), tuple(n for n, _ in required), held


def classify(
    p: Profile,
    ctx: ClassifyContext,
    s: Classifier,
    now: datetime,
    suppressed: frozenset[str] = frozenset(),
) -> list[Classification]:
    """`suppressed`: types an operator rejected for this address (D-059). They are never assigned,
    so the tests that exclude them (COLLECTOR is "not DEPOSIT") see them as absent."""
    found: dict[str, tuple[Decimal, tuple[str, ...], tuple[str, ...]]] = {}

    def assign(
        name: str, result: tuple[Decimal, tuple[str, ...], tuple[str, ...]] | None, floor: Decimal
    ) -> None:
        if name not in suppressed and result is not None and result[0] >= floor:
            found[name] = result

    m = s.min_confidence
    assign("CONTRACT", _check([("is_contract", p.is_contract)], "1.0", []), m)
    assign(
        "HUB",
        _check(
            [
                (
                    f"capped or counterparties ≥ {s.hub_min_counterparties}",
                    p.capped or p.counterparties >= s.hub_min_counterparties,
                )
            ],
            "0.7",
            [("capped", p.capped, "0.25")],
        ),
        m,
    )
    deposit = _check(
        [
            (
                f"distinct_senders ≥ {s.deposit_min_senders}",
                p.distinct_senders >= s.deposit_min_senders,
            ),
            (
                f"top_recipient_share_out ≥ {s.deposit_top_recipient_share}",
                _ge(p.top_recipient_share_out, s.deposit_top_recipient_share),
            ),
            ("top recipient is a hub or a labelled exchange/service", ctx.top_recipient_is_hub),
            (
                f"median_hold_hours ≤ {s.deposit_max_hold_hours}",
                _le(p.median_hold_hours, s.deposit_max_hold_hours),
            ),
            (
                f"retained_share ≤ {s.deposit_max_retained_share}",
                _le(p.retained_share, s.deposit_max_retained_share),
            ),
            ("n_out ≤ n_in", p.n_out <= p.n_in),
        ],
        "0.6",
        [
            (
                f"distinct_senders ≥ {s.deposit_bonus_min_senders}",
                p.distinct_senders >= s.deposit_bonus_min_senders,
                "0.1",
            ),
            (
                f"median_hold_hours ≤ {s.deposit_bonus_max_hold_hours}",
                _le(p.median_hold_hours, s.deposit_bonus_max_hold_hours),
                "0.1",
            ),
            (
                f"top_recipient_share_out ≥ {s.deposit_bonus_top_recipient_share}",
                _ge(p.top_recipient_share_out, s.deposit_bonus_top_recipient_share),
                "0.1",
            ),
            ("hub's entity named by the operator", ctx.hub_entity_named, "0.1"),
        ],
    )
    assign("DEPOSIT", deposit, m)
    is_deposit = "DEPOSIT" in found
    assign(
        "COLLECTOR",
        _check(
            [
                (
                    f"distinct_senders ≥ {s.collector_min_senders}",
                    p.distinct_senders >= s.collector_min_senders,
                ),
                (
                    f"small_in_share ≥ {s.collector_small_in_share}",
                    _ge(p.small_in_share, s.collector_small_in_share),
                ),
                (
                    f"top_recipient_share_out ≥ {s.collector_top_recipient_share}",
                    _ge(p.top_recipient_share_out, s.collector_top_recipient_share),
                ),
                ("not DEPOSIT", not is_deposit),
            ],
            "0.6",
            [
                (
                    f"distinct_senders ≥ {s.collector_bonus_min_senders}",
                    p.distinct_senders >= s.collector_bonus_min_senders,
                    "0.1",
                ),
                (
                    f"small_in_share ≥ {s.collector_bonus_small_in_share}",
                    _ge(p.small_in_share, s.collector_bonus_small_in_share),
                    "0.1",
                ),
                (
                    f"median_hold_hours ≤ {s.collector_bonus_max_hold_hours}",
                    _le(p.median_hold_hours, s.collector_bonus_max_hold_hours),
                    "0.1",
                ),
                (
                    "top recipient is FRESH or PASS_THROUGH",
                    ctx.top_recipient_fresh_or_pass_through,
                    "0.1",
                ),
            ],
        ),
        m,
    )
    assign(
        "DISTRIBUTOR",
        _check(
            [
                (
                    f"max_recipients_24h ≥ {s.distributor_min_recipients_24h}",
                    p.max_recipients_24h >= s.distributor_min_recipients_24h,
                ),
                (
                    f"distinct_senders ≤ {s.distributor_max_senders}",
                    p.distinct_senders <= s.distributor_max_senders,
                ),
            ],
            "0.6",
            [
                (
                    f"max_recipients_24h ≥ {s.distributor_bonus_min_recipients_24h}",
                    p.max_recipients_24h >= s.distributor_bonus_min_recipients_24h,
                    "0.2",
                ),
                (
                    f"round_share ≥ {s.distributor_bonus_round_share}",
                    _ge(p.round_share, s.distributor_bonus_round_share),
                    "0.2",
                ),
            ],
        ),
        m,
    )
    assign(
        "PASS_THROUGH",
        _check(
            [
                (
                    f"pass_through_share_24h ≥ {s.pass_through_share}",
                    _ge(p.pass_through_share_24h, s.pass_through_share),
                ),
                (
                    f"volume_in ≥ {s.pass_through_min_volume_usdt}",
                    p.volume_in >= s.pass_through_min_volume_usdt,
                ),
                ("not DEPOSIT", not is_deposit),
            ],
            "0.6",
            [
                (
                    f"pass_through_share_24h ≥ {s.pass_through_bonus_share}",
                    _ge(p.pass_through_share_24h, s.pass_through_bonus_share),
                    "0.2",
                ),
                (
                    f"median_hold_hours ≤ {s.pass_through_bonus_max_hold_hours}",
                    _le(p.median_hold_hours, s.pass_through_bonus_max_hold_hours),
                    "0.2",
                ),
            ],
        ),
        m,
    )
    if not found:
        assign(
            "PERSONAL",
            _check(
                [
                    (
                        f"counterparties ≤ {s.personal_max_counterparties}",
                        p.counterparties <= s.personal_max_counterparties,
                    )
                ],
                str(s.personal_confidence),
                [],
            ),
            s.personal_confidence,
        )
    primary = next((t for t in TYPES if t in found), None)
    out = [
        Classification(t, found[t][0], t == primary, found[t][1], found[t][2])
        for t in TYPES
        if t in found
    ]
    fresh = p.first_seen is not None and now - p.first_seen < timedelta(days=s.fresh_days)
    if fresh and FRESH not in suppressed:
        out.append(
            Classification(FRESH, Decimal(1), False, (f"first seen < {s.fresh_days} days ago",))
        )
    return out


def primary(classifications: Sequence[Classification]) -> Classification | None:
    return next((c for c in classifications if c.primary), None)


# --- storage ---------------------------------------------------------------------------------


def profile_json(p: Profile) -> dict[str, Any]:
    def conv(v: Any) -> Any:
        if isinstance(v, Decimal):
            return canonical_amount(v.quantize(Decimal("0.000001")))
        if isinstance(v, datetime):
            return to_db(v)
        return v

    return {k: conv(v) for k, v in asdict(p).items()}


_DECIMALS = frozenset(f.name for f in fields(Profile) if "Decimal" in str(f.type))
_TIMES = frozenset(f.name for f in fields(Profile) if "datetime" in str(f.type))


def profile_from_json(d: dict[str, Any]) -> Profile:
    """The inverse of `profile_json` (golden-set replay, D-067); decimals as stored (6 places)."""

    def conv(k: str, v: Any) -> Any:
        if v is None:
            return None
        if k in _DECIMALS:
            return Decimal(str(v))
        if k in _TIMES:
            return from_iso(str(v))
        return v

    return Profile(**{f.name: conv(f.name, d.get(f.name)) for f in fields(Profile)})


def save(
    conn: sqlite3.Connection,
    chain: Chain,
    p: Profile,
    classifications: Sequence[Classification],
    s: Classifier,
    now: datetime,
) -> None:
    expires = now + timedelta(days=s.ttl_days)
    features = profile_json(p)
    with transaction(conn):
        conn.executemany(
            "INSERT INTO classifications (chain, address_norm, type, is_primary, confidence, "
            "features_json, window_since, window_until, classifier_version, computed_at, "
            "expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    chain.value,
                    p.address,
                    c.type,
                    int(c.primary),
                    canonical_amount(c.confidence),
                    json.dumps(
                        {"profile": features, "conditions": c.conditions, "bonuses": c.bonuses},
                        sort_keys=True,
                    ),
                    to_db(p.since),
                    to_db(p.until),
                    CLASSIFIER_VERSION,
                    to_db(now),
                    to_db(expires),
                )
                for c in classifications
            ],
        )


def suppressed(conn: sqlite3.Connection, chain: Chain, address: str) -> frozenset[str]:
    """Types whose latest operator feedback for this classifier version is a rejection (D-059).
    A new classifier version starts clean (AT-50)."""
    rows = conn.execute(
        "SELECT type, verdict FROM inference_feedback WHERE chain = ? AND address_norm = ? "
        "AND classifier_version = ? ORDER BY id",
        (chain.value, address, CLASSIFIER_VERSION),
    ).fetchall()
    latest: dict[str, str] = {}
    for t, verdict in rows:
        latest[t] = verdict
    return frozenset(t for t, v in latest.items() if v == "rejected")


def cached(
    conn: sqlite3.Connection, chain: Chain, address: str, now: datetime
) -> list[Classification] | None:
    """The latest unexpired classification of this version, or None (F8.2: 14-day expiry).
    One that holds a type the operator has since rejected is stale: None, so it is recomputed."""
    row = conn.execute(
        "SELECT max(computed_at) FROM classifications WHERE chain = ? AND address_norm = ? "
        "AND classifier_version = ? AND expires_at > ?",
        (chain.value, address, CLASSIFIER_VERSION, to_db(now)),
    ).fetchone()
    if row is None or row[0] is None:
        return None
    rows = conn.execute(
        "SELECT type, confidence, is_primary, features_json FROM classifications "
        "WHERE chain = ? AND address_norm = ? AND computed_at = ? ORDER BY id",
        (chain.value, address, row[0]),
    ).fetchall()
    if {r[0] for r in rows} & suppressed(conn, chain, address):
        return None
    out = []
    for t, conf, is_primary, fj in rows:
        data = json.loads(fj)
        out.append(
            Classification(
                t,
                Decimal(conf),
                bool(is_primary),
                tuple(data["conditions"]),
                tuple(data["bonuses"]),
            )
        )
    return out


def computed_at(conn: sqlite3.Connection, chain: Chain, address: str) -> datetime | None:
    row = conn.execute(
        "SELECT max(computed_at) FROM classifications WHERE chain = ? AND address_norm = ?",
        (chain.value, address),
    ).fetchone()
    return from_iso(row[0]) if row and row[0] else None
