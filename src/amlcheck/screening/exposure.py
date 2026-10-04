"""Exposure and behaviour: the target's 180-day USDT history (PRD F5, methodology §3).

One required source per check. It reads the history through the transfer cache, builds the
counterparties, flags them from **local data only** (sanctions snapshot, TRON freeze index,
labels.csv; no API quota spent on neighbours, D-015), and raises:

- R-EXP-01: a counterparty is sanctioned or frozen (REVIEW by default, D-014), at most 10, largest
  first;
- R-EXP-02: ≥ `[exposure] flagged_inflow_share` of USDT received came from flagged counterparties
  (sanctioned, frozen, or a risky tag, D-042);
- R-HEU-01: first activity less than `new_address_days` ago, or no activity at all (low priority);
- R-HEU-02: pass-through, ≥ 90% of USDT received left within 24 h (FIFO);
- R-HEU-03: fan-in, > 50 distinct senders each sending < 100 USDT within any 24 h;
- R-HEU-04: fan-out, > 50 distinct recipients within any 24 h;
- R-HEU-05: a counterparty carries a risky tag (mixer, bridge, high_risk), at most 10.

Its evidence also carries the **direct exposures** (methodology §11.1): every counterparty with a
risk category, in each direction it dealt in, with the exact amount and its share of that
direction's flow.

`allowlist` counterparties are left out of R-HEU-02…04 and never cancel R-EXP-01 (F5.4).
More than `[exposure] max_transfers` in the window → `stale` → INCOMPLETE (D-013, AT-26); findings
from the part that was read are still reported (they are true), but the check can't end clean.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from amlcheck.chain.base import History, Transfer, canonical_amount
from amlcheck.chain.cache import TransferCache
from amlcheck.config import Exposure, Heuristics
from amlcheck.core import risk
from amlcheck.core.clock import Clock, to_iso, utcnow
from amlcheck.core.models import Address, Chain, Finding, SourceResult, SourceStatus
from amlcheck.core.rules import finding
from amlcheck.intel.labels_csv import tags_for
from amlcheck.intel.names import entity_name
from amlcheck.screening.base import SourceHealth
from amlcheck.screening.heuristics import busiest_window, pass_through, recipients, senders

SOURCE = "exposure"
LABEL = "USDT history (exposure)"
SHOWN_COUNTERPARTIES = 20
_CHUNK = 500


@dataclass
class Counterparty:
    address: str
    received: Decimal = Decimal(0)  # by the target, from this counterparty
    sent: Decimal = Decimal(0)  # by the target, to this counterparty
    transfers: list[Transfer] = field(default_factory=list)
    flags: set[str] = field(default_factory=set)  # sanctioned | frozen | label:<tag>

    @property
    def volume(self) -> Decimal:
        return self.received + self.sent

    def summary(self, address: str) -> dict[str, Any]:
        largest = sorted(self.transfers, key=lambda t: (-t.amount, t.time, t.tx_hash, t.idx))[:3]
        return {
            "address": self.address,
            "received_usdt": canonical_amount(self.received),
            "sent_usdt": canonical_amount(self.sent),
            "transfers": len(self.transfers),
            "flags": sorted(self.flags),
            "largest": [
                {
                    "tx_hash": t.tx_hash,
                    "time": to_iso(t.time),
                    "direction": "in" if t.recipient == address else "out",
                    "amount_usdt": canonical_amount(t.amount),
                }
                for t in largest
            ],
        }


def counterparties(address: str, transfers: Iterable[Transfer]) -> dict[str, Counterparty]:
    out: dict[str, Counterparty] = {}
    for t in transfers:
        if t.sender == t.recipient:
            continue  # self-transfers are ignored (methodology §3.2)
        if t.recipient == address:
            cp = out.setdefault(t.sender, Counterparty(t.sender))
            cp.received += t.amount
        elif t.sender == address:
            cp = out.setdefault(t.recipient, Counterparty(t.recipient))
            cp.sent += t.amount
        else:
            continue
        cp.transfers.append(t)
    return out


def _by_size(cps: Iterable[Counterparty]) -> list[Counterparty]:
    return sorted(cps, key=lambda c: (-c.volume, c.address))


def local_flags(
    conn: sqlite3.Connection, chain: Chain, addresses: Iterable[str]
) -> Mapping[str, set[str]]:
    """sanctioned (latest OFAC snapshot), frozen (TRON index: latest blacklist event is an add),
    label:<tag> (labels.csv tags and active intel-label categories). Local data only (D-015)."""
    wanted = sorted(set(addresses))
    flags: dict[str, set[str]] = {}
    snap = conn.execute(
        "SELECT id FROM list_snapshots WHERE source = 'ofac_sdn' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    for i in range(0, len(wanted), _CHUNK):
        chunk = wanted[i : i + _CHUNK]
        marks = ",".join("?" * len(chunk))
        if snap:
            for (a,) in conn.execute(
                "SELECT DISTINCT address_norm FROM sanctioned_addresses "  # noqa: S608 - placeholders only
                f"WHERE snapshot_id = ? AND address_norm IN ({marks})",
                [snap[0], *chunk],
            ):
                flags.setdefault(a, set()).add("sanctioned")
        if chain is Chain.TRON:
            for a, event in conn.execute(
                "SELECT address_norm, event_type FROM issuer_events e "  # noqa: S608 - placeholders only
                "WHERE chain = 'tron' AND event_type IN ('AddedBlackList', 'RemovedBlackList') "
                f"AND address_norm IN ({marks}) "
                "AND NOT EXISTS (SELECT 1 FROM issuer_events x WHERE x.chain = e.chain "
                "AND x.token_contract = e.token_contract AND x.address_norm = e.address_norm "
                "AND x.event_type IN ('AddedBlackList', 'RemovedBlackList') "
                "AND (x.block > e.block OR (x.block = e.block AND x.event_index > e.event_index)))",
                chunk,
            ):
                if event == "AddedBlackList":
                    flags.setdefault(a, set()).add("frozen")
    for a, tags in tags_for(conn, chain, wanted).items():
        flags.setdefault(a, set()).update(f"label:{t}" for t in tags)
    for i in range(0, len(wanted), _CHUNK):
        chunk = wanted[i : i + _CHUNK]
        marks = ",".join("?" * len(chunk))
        for a, category in conn.execute(
            "SELECT address_norm, category FROM intel_labels "  # noqa: S608 - placeholders only
            f"WHERE chain = ? AND retracted_at IS NULL AND address_norm IN ({marks})",
            [chain.value, *chunk],
        ):
            flags.setdefault(a, set()).add(f"label:{category}")
    return flags


class ExposureSource:
    source = SOURCE
    label = LABEL
    required = True
    timeout: float | None = None

    def __init__(
        self,
        cache: TransferCache,
        conn: sqlite3.Connection,
        exposure: Exposure,
        heuristics: Heuristics,
        *,
        clock: Clock = utcnow,
    ) -> None:
        self._cache = cache
        self._conn = conn
        self._exp = exposure
        self._heu = heuristics
        self._clock = clock

    async def check(self, address: Address) -> SourceResult:
        now = self._clock()
        since = now - timedelta(days=self._exp.lookback_days)
        history = await self._cache.history(
            address.norm, since, None, self._exp.max_transfers, first_activity=True
        )
        findings, evidence = self.assess(address, history, now)
        n = len(history.transfers)
        detail = (
            f"{n} transfer(s) with {evidence['counterparty_count']} counterparties since "
            f"{to_iso(since)[:10]}"
        )
        status = SourceStatus.OK
        if not history.complete:
            status = SourceStatus.STALE
            detail = (
                f"more than {self._exp.max_transfers} transfers since {to_iso(since)[:10]}; "
                f"only the newest {n} were read, so the history is incomplete (D-013)"
            )
        return SourceResult(SOURCE, LABEL, True, status, now, tuple(findings), detail, evidence)

    def assess(
        self, address: Address, history: History, now: datetime
    ) -> tuple[list[Finding], dict[str, Any]]:
        a = address.norm
        cps = counterparties(a, history.transfers)
        for cp_address, f in local_flags(self._conn, address.chain, cps).items():
            if cp_address in cps:
                cps[cp_address].flags |= f
        risky = {f"label:{t}" for t in self._heu.risky_tags}
        # An own_or_trusted intel label means what the allowlist tag means (methodology §8).
        allow = {f"label:{self._heu.allowlist_tag}", "label:own_or_trusted"}
        findings: list[Finding] = []

        # R-EXP-01 · direct counterparty sanctioned or frozen
        hit = [c for c in _by_size(cps.values()) if c.flags & {"sanctioned", "frozen"}]
        for c in hit[: self._exp.max_findings]:
            what = " and ".join(sorted(c.flags & {"sanctioned", "frozen"}))
            findings.append(
                finding(
                    "R-EXP-01",
                    SOURCE,
                    f"Dealt directly with a {what} address {c.address} (received "
                    f"{canonical_amount(c.received)}, sent {canonical_amount(c.sent)} USDT)",
                    now,
                    {"counterparty": c.summary(a)},
                )
            )

        # R-EXP-02 · share of inflow from flagged counterparties (D-042)
        received = sum((c.received for c in cps.values()), Decimal(0))
        flagged = [c for c in cps.values() if c.flags & ({"sanctioned", "frozen"} | risky)]
        from_flagged = sum((c.received for c in flagged), Decimal(0))
        if received > 0 and from_flagged / received >= self._exp.flagged_inflow_share:
            share = from_flagged / received
            findings.append(
                finding(
                    "R-EXP-02",
                    SOURCE,
                    f"{_pct(share)} of USDT received ({canonical_amount(from_flagged)} of "
                    f"{canonical_amount(received)}) came from flagged counterparties",
                    now,
                    {
                        "share": _share(share),
                        "from_flagged_usdt": canonical_amount(from_flagged),
                        "received_usdt": canonical_amount(received),
                        "flagged": [
                            {"address": c.address, "flags": sorted(c.flags)}
                            for c in _by_size(flagged)[: self._exp.max_findings]
                        ],
                    },
                )
            )

        # R-HEU-01 · new address (low priority)
        times = [t.time for t in history.transfers]
        first = min(
            [*times, *([history.first_activity] if history.first_activity else [])], default=None
        )
        if first is None:
            findings.append(
                finding(
                    "R-HEU-01",
                    SOURCE,
                    "No activity found for this address",
                    now,
                    {"priority": "low", "first_activity": None},
                )
            )
        elif now - first < timedelta(days=self._heu.new_address_days):
            findings.append(
                finding(
                    "R-HEU-01",
                    SOURCE,
                    f"New address: first activity {to_iso(first)}",
                    now,
                    {"priority": "low", "first_activity": to_iso(first)},
                )
            )

        # Behaviour rules leave allowlisted counterparties out (F5.4).
        def counted(t: Transfer) -> bool:
            other = t.recipient if t.sender == a else t.sender
            return not allow & cps.get(other, Counterparty(other)).flags

        behaviour = [t for t in history.transfers if counted(t)]

        # R-HEU-02 · pass-through
        pt = pass_through(a, behaviour, timedelta(hours=self._heu.pass_through_hours))
        if pt.share is not None and pt.share >= self._heu.pass_through_share:
            findings.append(
                finding(
                    "R-HEU-02",
                    SOURCE,
                    f"Pass-through: {_pct(pt.share)} of USDT received left within "
                    f"{self._heu.pass_through_hours} h",
                    now,
                    {
                        "share": _share(pt.share),
                        "received_usdt": canonical_amount(pt.received),
                        "passed_usdt": canonical_amount(pt.passed),
                    },
                )
            )

        window = timedelta(hours=self._heu.fan_window_hours)
        # R-HEU-03 · fan-in of small payments
        small = self._heu.fan_in_small_usdt
        n_in, start_in = busiest_window(senders(a, behaviour, lambda t: t.amount < small), window)
        if n_in > self._heu.fan_in_senders:
            findings.append(
                finding(
                    "R-HEU-03",
                    SOURCE,
                    f"Fan-in: {n_in} distinct senders of under {canonical_amount(small)} USDT "
                    f"within {self._heu.fan_window_hours} h",
                    now,
                    {"senders": n_in, "window_start": to_iso(start_in) if start_in else None},
                )
            )
        # R-HEU-04 · fan-out
        n_out, start_out = busiest_window(recipients(a, behaviour), window)
        if n_out > self._heu.fan_out_recipients:
            findings.append(
                finding(
                    "R-HEU-04",
                    SOURCE,
                    f"Fan-out: {n_out} distinct recipients within {self._heu.fan_window_hours} h",
                    now,
                    {"recipients": n_out, "window_start": to_iso(start_out) if start_out else None},
                )
            )

        # R-HEU-05 · dealt with a risky-tagged counterparty
        tagged = [c for c in _by_size(cps.values()) if c.flags & risky]
        for c in tagged[: self._heu.max_findings]:
            tags = ", ".join(sorted(f.removeprefix("label:") for f in c.flags & risky))
            findings.append(
                finding(
                    "R-HEU-05",
                    SOURCE,
                    f"Dealt with {c.address}, labelled {tags}",
                    now,
                    {"counterparty": c.summary(a)},
                )
            )

        sent = sum((c.sent for c in cps.values()), Decimal(0))
        exposures = direct_exposures(
            a,
            cps.values(),
            received,
            sent,
            lambda cp, category: entity_name(self._conn, address.chain, cp, category),
        )
        evidence: dict[str, Any] = {
            "since": to_iso(history.since),
            "until": to_iso(history.until),
            "transfers": len(history.transfers),
            "complete": history.complete,
            "zero_value_dropped": history.zero_value,
            "first_activity": to_iso(first) if first else None,
            "received_usdt": canonical_amount(received),
            "sent_usdt": canonical_amount(sent),
            "counterparty_count": len(cps),
            "counterparties": [c.summary(a) for c in _by_size(cps.values())[:SHOWN_COUNTERPARTIES]],
            "exposures": [e.to_json() for e in exposures],
        }
        return findings, evidence

    async def health(self) -> SourceHealth:
        return SourceHealth(SOURCE, LABEL, SourceStatus.OK, None, "read per check")


def direct_exposures(
    address: str,
    cps: Iterable[Counterparty],
    received: Decimal,
    sent: Decimal,
    name: Callable[[str, str], str],
) -> list[risk.Exposure]:
    """Hop-1 exposures (methodology §11.1): one per flagged counterparty and direction, its most
    severe risk category, the exact amount, and its share of everything received or sent."""
    out: list[risk.Exposure] = []
    for c in cps:
        category = risk.risk_category(c.flags)
        if category is None:
            continue
        entity = name(c.address, category)
        for direction, amount, total in (("in", c.received, received), ("out", c.sent, sent)):
            if amount > 0 and total > 0:
                out.append(
                    risk.Exposure(
                        direction,
                        1,
                        c.address,
                        category,
                        entity,
                        amount,
                        amount / total,
                        (address, c.address),
                    )
                )
    return risk.ordered(out)


def _share(x: Decimal) -> str:
    return canonical_amount(x.quantize(Decimal("0.0001")))


def _pct(x: Decimal) -> str:
    return f"{(x * 100).quantize(Decimal('0.1'))}%"
