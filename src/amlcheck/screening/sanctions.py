"""OFAC SDN sanctions list: download, parse, snapshots, adapter (PRD F3, methodology §2.2).

- Every `Digital Currency Address - XXX` id is parsed whatever its currency label: labels are
  unreliable (TRON addresses under XBT, an EVM address under ARB/BSC/ETH), so matching is on the
  address string only (D-006, VS-01). `0x` addresses are stored lowercase, so an address listed
  under any EVM label matches on BSC (F3.2).
- A listed address that fails its own checksum is kept (a typo must not hide an entry) and flagged.
- A new snapshot with more than 20% fewer addresses than the last is rejected and the old one kept
  (F3.4, AT-21).
- Freshness is measured from **our** last successful download, not OFAC's publish date (D-007):
  older than `[freshness] sanctions_max_age_hours` → `stale` → INCOMPLETE, though a listing still
  blocks (AT-15).
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
import sqlite3
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from amlcheck.config import Freshness, Ofac
from amlcheck.core.address import eip55, is_valid_tron
from amlcheck.core.clock import Clock, from_iso, to_db, to_iso, utcnow
from amlcheck.core.models import Address, Finding, SourceResult, SourceStatus
from amlcheck.core.rules import finding
from amlcheck.net.http import Http, Provider, SourceError
from amlcheck.screening.base import SourceHealth
from amlcheck.storage.db import transaction

log = logging.getLogger("amlcheck.sanctions")

SOURCE = "ofac_sdn"
LABEL = "OFAC SDN list"
_PREFIX = "Digital Currency Address - "
_TRON = re.compile(r"^T[1-9A-HJ-NP-Za-km-z]{33}$")
_EVM = re.compile(r"^0x[0-9a-fA-F]{40}$")
KEEP_SNAPSHOTS_WITH_ADDRESSES = 3


@dataclass(frozen=True)
class ListedAddress:
    address_norm: str
    currency_label: str
    entry_id: str
    entity_name: str | None
    program: str | None
    checksum_ok: bool


@dataclass(frozen=True)
class ParsedList:
    published_at: str | None  # ISO date
    entry_count: int
    addresses: tuple[ListedAddress, ...]


@dataclass(frozen=True)
class SyncResult:
    accepted: bool
    address_count: int
    previous_count: int | None
    published_at: str | None
    sha256: str
    reason: str | None = None


def normalise_listed(raw: str) -> tuple[str, bool]:
    """(stored form, checksum_ok). TRON kept as is; `0x` lowercased; anything else kept trimmed."""
    text = raw.strip()
    if _TRON.match(text):
        return text, is_valid_tron(text)
    if _EVM.match(text):
        body = text[2:]
        mixed = body != body.lower() and body != body.upper()
        return "0x" + body.lower(), (eip55(text) == text) if mixed else True
    return text, True


def parse_sdn(data: bytes) -> ParsedList:
    """Parse SDN.XML (namespace-agnostic, streaming)."""
    published: str | None = None
    entries = 0
    found: list[ListedAddress] = []
    try:
        for _, elem in ET.iterparse(io.BytesIO(data), events=("end",)):  # noqa: S314 - OFAC's own file, entities not expanded by ET
            tag = _local(elem.tag)
            if tag == "Publish_Date" and elem.text:
                published = _publish_date(elem.text)
            elif tag == "sdnEntry":
                entries += 1
                found.extend(_entry_addresses(elem))
                elem.clear()
    except ET.ParseError as e:
        raise SourceError(SOURCE, f"list is not valid XML ({e})") from None
    if entries == 0:
        raise SourceError(SOURCE, "list has no entries")
    return ParsedList(published, entries, tuple(found))


def _entry_addresses(entry: ET.Element) -> Iterator[ListedAddress]:
    fields = {_local(c.tag): c for c in entry}
    uid = (fields["uid"].text or "").strip() if "uid" in fields else ""
    first = (fields["firstName"].text or "").strip() if "firstName" in fields else ""
    last = (fields["lastName"].text or "").strip() if "lastName" in fields else ""
    name = f"{first} {last}".strip() or None
    programs = [
        (p.text or "").strip()
        for c in entry
        if _local(c.tag) == "programList"
        for p in c
        if (p.text or "").strip()
    ]
    program = ", ".join(sorted(programs)) or None
    for c in entry:
        if _local(c.tag) != "idList":
            continue
        for ident in c:
            parts = {_local(x.tag): (x.text or "").strip() for x in ident}
            id_type = parts.get("idType", "")
            number = parts.get("idNumber", "")
            if not id_type.startswith(_PREFIX) or not number:
                continue
            norm, ok = normalise_listed(number)
            if not ok:
                log.warning("OFAC entry %s lists %s, which fails its checksum; kept", uid, number)
            yield ListedAddress(norm, id_type[len(_PREFIX) :], uid, name, program, ok)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _publish_date(text: str) -> str | None:
    try:
        return datetime.strptime(text.strip(), "%m/%d/%Y").replace(tzinfo=UTC).date().isoformat()
    except ValueError:
        return None


async def download(http: Http, settings: Ofac) -> bytes:
    """GET SDN.XML; OFAC redirects to a short-lived signed URL, which is never logged."""
    resp = await http.request(Provider(SOURCE), "GET", settings.sdn_url, follow_redirects=True)
    return resp.content


def store(
    conn: sqlite3.Connection,
    data: bytes,
    parsed: ParsedList,
    settings: Ofac,
    now: datetime,
) -> SyncResult:
    """Accept the snapshot unless it lost too many addresses (F3.4)."""
    digest = hashlib.sha256(data).hexdigest()
    count = len(parsed.addresses)
    prev = conn.execute(
        "SELECT address_count FROM list_snapshots WHERE source = ? ORDER BY id DESC LIMIT 1",
        (SOURCE,),
    ).fetchone()
    prev_count = int(prev[0]) if prev else None
    if prev_count is not None and Decimal(count) < Decimal(prev_count) * settings.min_kept_share:
        reason = (
            f"new list has {count} addresses, {prev_count} before: more than "
            f"{(1 - settings.min_kept_share) * 100:.0f}% fewer; rejected, previous snapshot kept"
        )
        log.warning("OFAC snapshot rejected: %s", reason)
        return SyncResult(False, count, prev_count, parsed.published_at, digest, reason)
    with transaction(conn):
        cur = conn.execute(
            "INSERT INTO list_snapshots (source, fetched_at, published_at, sha256, entry_count, "
            "address_count) VALUES (?, ?, ?, ?, ?, ?)",
            (SOURCE, to_db(now), parsed.published_at, digest, parsed.entry_count, count),
        )
        snapshot_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO sanctioned_addresses (snapshot_id, address_norm, currency_label, "
            "list_entry_id, entity_name, program, checksum_ok) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    snapshot_id,
                    a.address_norm,
                    a.currency_label,
                    a.entry_id,
                    a.entity_name,
                    a.program,
                    int(a.checksum_ok),
                )
                for a in parsed.addresses
            ],
        )
        # Old snapshots stay as records (their hash is in audit evidence); their address rows don't
        # need to, beyond a few for comparison.
        conn.execute(
            "DELETE FROM sanctioned_addresses WHERE snapshot_id IN (SELECT id FROM list_snapshots "
            "WHERE source = ? ORDER BY id DESC LIMIT -1 OFFSET ?)",
            (SOURCE, KEEP_SNAPSHOTS_WITH_ADDRESSES),
        )
    return SyncResult(True, count, prev_count, parsed.published_at, digest)


async def sync(
    http: Http, conn: sqlite3.Connection, settings: Ofac, clock: Clock = utcnow
) -> SyncResult:
    data = await download(http, settings)
    return store(conn, data, parse_sdn(data), settings, clock())


@dataclass(frozen=True)
class _Snapshot:
    id: int
    fetched_at: datetime
    published_at: str | None
    sha256: str
    address_count: int


class SanctionsSource:
    source = SOURCE
    label = LABEL
    required = True
    timeout: float | None = None

    def __init__(
        self, conn: sqlite3.Connection, freshness: Freshness, *, clock: Clock = utcnow
    ) -> None:
        self._conn = conn
        self._max_age = timedelta(hours=freshness.sanctions_max_age_hours)
        self._clock = clock

    def _latest(self) -> _Snapshot | None:
        row = self._conn.execute(
            "SELECT id, fetched_at, published_at, sha256, address_count FROM list_snapshots "
            "WHERE source = ? ORDER BY id DESC LIMIT 1",
            (SOURCE,),
        ).fetchone()
        if row is None:
            return None
        return _Snapshot(row[0], from_iso(row[1]), row[2], row[3], row[4])

    def _freshness(self, snap: _Snapshot | None, now: datetime) -> tuple[SourceStatus, str]:
        if snap is None:
            return SourceStatus.STALE, "never downloaded; run `amlcheck sync`"
        age = now - snap.fetched_at
        hours = age.total_seconds() / 3600
        detail = (
            f"list of {snap.published_at or 'unknown date'}, downloaded {hours:.0f} h ago "
            f"({snap.address_count} addresses)"
        )
        if age > self._max_age:
            return SourceStatus.STALE, detail + "; run `amlcheck sync`"
        return SourceStatus.OK, detail

    async def check(self, address: Address) -> SourceResult:
        now = self._clock()
        snap = self._latest()
        status, detail = self._freshness(snap, now)
        findings: tuple[Finding, ...] = ()
        evidence: dict[str, object] = {}
        if snap is not None:
            evidence = {
                "snapshot_id": snap.id,
                "sha256": snap.sha256,
                "published": snap.published_at,
            }
            rows = self._conn.execute(
                "SELECT list_entry_id, entity_name, program, currency_label, checksum_ok "
                "FROM sanctioned_addresses WHERE address_norm = ? AND snapshot_id = ? "
                "ORDER BY list_entry_id, currency_label",
                (address.norm, snap.id),
            ).fetchall()
            if rows:
                labels: dict[str, list[str]] = {}
                entries: dict[str, dict[str, object]] = {}
                for entry_id, name, program, label, ok in rows:
                    labels.setdefault(entry_id, []).append(label)
                    entries.setdefault(
                        entry_id,
                        {
                            "entry_id": entry_id,
                            "entity_name": name,
                            "program": program,
                            "checksum_ok": bool(ok),
                        },
                    )
                for entry_id, e in entries.items():
                    e["currency_labels"] = labels[entry_id]
                first = next(iter(entries.values()))
                summary = (
                    f"Listed on the OFAC SDN list: {first['entity_name'] or 'unnamed entry'} "
                    f"(entry {first['entry_id']}, {first['program'] or 'no program'})"
                )
                if len(entries) > 1:
                    summary += (
                        f" and {len(entries) - 1} more entr{'y' if len(entries) == 2 else 'ies'}"
                    )
                findings = (
                    finding(
                        "R-SAN-01",
                        SOURCE,
                        summary,
                        now,
                        {
                            "list": "OFAC SDN",
                            "entries": list(entries.values()),
                            "snapshot": {
                                "id": snap.id,
                                "sha256": snap.sha256,
                                "published": snap.published_at,
                                "downloaded": to_iso(snap.fetched_at),
                            },
                        },
                    ),
                )
        return SourceResult(
            source=SOURCE,
            label=LABEL,
            required=True,
            status=status,
            observed_at=now,
            findings=findings,
            detail=detail,
            evidence=evidence,
        )

    async def health(self) -> SourceHealth:
        snap = self._latest()
        status, detail = self._freshness(snap, self._clock())
        return SourceHealth(SOURCE, LABEL, status, snap.fetched_at if snap else None, detail)
