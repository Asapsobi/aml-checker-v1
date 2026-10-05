"""Sanctions lists beyond OFAC: UK, EU and NBCTF (methodology §13.1, D-084, D-085).

They share OFAC's snapshot store and adapter (`screening.sanctions`): a listing is R-SAN-01, BLOCK,
naming the list. Their addresses appear only in free text (VS-18, VS-19), so every TRON or `0x`
address in an entry's text is taken and kept only when its checksum holds: a typo in a government
list must not make an unrelated address match.
"""

from __future__ import annotations

import io
import re
import sqlite3
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from amlcheck.core.address import is_valid_tron
from amlcheck.net.http import Http, Provider, SourceError
from amlcheck.net.http import download as download_file
from amlcheck.screening.sanctions import ListedAddress, ParsedList, SyncResult, store

_TRON = re.compile(r"(?<![1-9A-HJ-NP-Za-km-z])T[1-9A-HJ-NP-Za-km-z]{33}(?![1-9A-HJ-NP-Za-km-z])")
_EVM = re.compile(r"(?<![0-9A-Za-z])0x[0-9a-fA-F]{40}(?![0-9A-Za-z])")


@dataclass(frozen=True)
class ListSpec:
    source: str  # list_snapshots.source
    label: str  # the source's label in checks
    list_name: str  # in R-SAN-01 and entity names
    required: bool  # a stale or missing list makes checks INCOMPLETE


UK = ListSpec("uk_sanctions", "UK Sanctions List", "UK sanctions", True)
EU = ListSpec("eu_sanctions", "EU sanctions list", "EU sanctions", True)
NBCTF = ListSpec("nbctf", "NBCTF seizure orders", "NBCTF", False)
#: The EU file's published, shared access parameter (VS-19): the same for everyone, not a secret;
#: kept out of config so config stays free of anything that looks like a credential.
EU_PUBLIC_ACCESS = "?token=dG9rZW4tMjAxNw"
#: Name order when an address is on several lists: OFAC first (methodology §13.1).
ORDER = ("ofac_sdn", UK.source, EU.source, NBCTF.source)
NAMES = {
    "ofac_sdn": "OFAC SDN",
    UK.source: UK.list_name,
    EU.source: EU.list_name,
    NBCTF.source: NBCTF.list_name,
}


def addresses_in(text: str) -> list[str]:
    """TRON and `0x` addresses in free text whose checksum holds, in order, without repeats;
    `0x` lowercased (as OFAC's are stored, F3.2)."""
    found: list[str] = []
    for m in _TRON.finditer(text):
        if is_valid_tron(m.group()) and m.group() not in found:
            found.append(m.group())
    for m in _EVM.finditer(text):
        a = m.group().lower()
        if a not in found:
            found.append(a)
    return found


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _dmy(text: str | None) -> str | None:
    """`02/10/2026` → `2026-10-02`."""
    if not text:
        return None
    try:
        return datetime.strptime(text.strip(), "%d/%m/%Y").replace(tzinfo=UTC).date().isoformat()
    except ValueError:
        return None


def _listed(
    addresses: Iterable[str], entry: str, name: str | None, program: str | None
) -> Iterator[ListedAddress]:
    for a in addresses:
        yield ListedAddress(a, "TRX" if a.startswith("T") else "EVM", entry, name, program, True)


def parse_uk(data: bytes) -> ParsedList:
    """The FCDO UK Sanctions List XML (VS-18): one `<Designation>` per entry."""
    published: str | None = None
    entries = 0
    found: list[ListedAddress] = []
    try:
        for _, elem in ET.iterparse(io.BytesIO(data), events=("end",)):  # noqa: S314 - a government list, entities not expanded by ET
            tag = _local(elem.tag)
            if tag == "DateGenerated":
                published = _dmy(elem.text)
            elif tag == "Designation":
                entries += 1
                fields = {_local(c.tag): c for c in elem}
                entry = (fields["UniqueID"].text or "").strip() if "UniqueID" in fields else ""
                program = fields["RegimeName"].text if "RegimeName" in fields else None
                text = " ".join(elem.itertext())
                found.extend(_listed(addresses_in(text), entry, _uk_name(elem), program))
                elem.clear()
    except ET.ParseError as e:
        raise SourceError(UK.source, f"list is not valid XML ({e})") from None
    if entries == 0:
        raise SourceError(UK.source, "list has no designations")
    return ParsedList(published, entries, tuple(found))


def _uk_name(designation: ET.Element) -> str | None:
    """The primary name (`NameType` "Primary Name"), else the first."""
    names = [n for n in designation.iter() if _local(n.tag) == "Name"]

    def text(n: ET.Element, field: str) -> str:
        return next((c.text or "" for c in n if _local(c.tag) == field), "").strip()

    primary = [n for n in names if text(n, "NameType") == "Primary Name"]
    chosen = (primary or names)[:1]
    return text(chosen[0], "Name6") or None if chosen else None


def parse_eu(data: bytes) -> ParsedList:
    """The EU Financial Sanctions Files XML (VS-19): one `<sanctionEntity>` per entry."""
    published: str | None = None
    entries = 0
    found: list[ListedAddress] = []
    try:
        for event, elem in ET.iterparse(io.BytesIO(data), events=("start", "end")):  # noqa: S314 - the Commission's own file
            tag = _local(elem.tag)
            if event == "start":
                if tag == "export" and elem.get("generationDate"):
                    published = elem.get("generationDate", "")[:10] or None
                continue
            if tag != "sanctionEntity":
                continue
            entries += 1
            entry = elem.get("euReferenceNumber") or elem.get("logicalId") or ""
            aliases = [a for a in elem if _local(a.tag) == "nameAlias" and a.get("wholeName")]
            english = [a for a in aliases if a.get("nameLanguage") == "EN"]
            name = (english or aliases)[0].get("wholeName") if aliases else None
            program = next(
                (r.get("programme") for r in elem if _local(r.tag) == "regulation"), None
            )
            text = " ".join(elem.itertext())
            found.extend(_listed(addresses_in(text), entry, name, program))
            elem.clear()
    except ET.ParseError as e:
        raise SourceError(EU.source, f"list is not valid XML ({e})") from None
    if entries == 0:
        raise SourceError(EU.source, "list has no entities")
    return ParsedList(published, entries, tuple(found))


async def sync_list(
    http: Http,
    conn: sqlite3.Connection,
    spec: ListSpec,
    url: str,
    min_kept_share: Decimal,
    now: datetime,
) -> SyncResult:
    """Download, parse and store one list; one that lost too many addresses is rejected (F3.4)."""
    if spec is EU and "?" not in url:
        url += EU_PUBLIC_ACCESS
    data = await download_file(http, Provider(spec.source), url)
    parsed = parse_uk(data) if spec is UK else parse_eu(data)
    return store(conn, data, parsed, min_kept_share, now, source=spec.source)
