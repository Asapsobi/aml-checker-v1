"""Sanctions lists beyond OFAC: UK, EU and NBCTF (methodology §13.1, D-084, D-085).

They share OFAC's snapshot store and adapter (`screening.sanctions`): a listing is R-SAN-01, BLOCK,
naming the list. Their addresses appear only in free text (VS-18, VS-19, VS-20), so every TRON or
`0x` address in an entry's text is taken and kept only when its checksum holds: a typo in a
government list must not make an unrelated address match.
"""

from __future__ import annotations

import csv
import io
import re
import sqlite3
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

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


#: Cyrillic and Greek letters that look like Latin ones. NBCTF's FO 02/24 lists a TRON address typed
#: with a Cyrillic "Н" and "с" (VS-20). Read as Latin, a string is kept only when the TRON checksum
#: then holds, so a look-alike can't make an unrelated address match. `0x` addresses have no
#: checksum to confirm a reading, so they are never read this way.
_LATIN = str.maketrans(
    # Cyrillic capitals, Cyrillic small, Greek capitals, Greek small
    "АВЕЗІЈКМНОРСЅТУХаеіјорсѕухԁһԛԝΑΒΕΖΗΙΚΜΝΟΡΤΥΧανορυ",
    "ABE3IJKMHOPCSTYXaeijopcsyxdhqwABEZHIKMNOPTYXavopu",
)


def _scan(text: str) -> tuple[list[str], list[str]]:
    """TRON and `0x` addresses in free text whose checksum holds, in order, without repeats, `0x`
    lowercased (as OFAC's are stored, F3.2); and the TRON-looking strings whose checksum fails,
    as written."""
    found: dict[str, None] = {}
    failed: dict[str, None] = {}
    # A second reading with look-alikes made Latin; the first keeps every plain match, since making
    # a neighbouring letter Latin can join it to an address.
    readings = [text] if text.isascii() else [text, text.translate(_LATIN)]
    for reading in readings:
        for m in _TRON.finditer(reading):
            if is_valid_tron(m.group()):
                found.setdefault(m.group())
            else:
                failed.setdefault(text[m.start() : m.end()])  # same length: the original text
    for m in _EVM.finditer(text):
        found.setdefault(m.group().lower())
    return list(found), list(failed)


def addresses_in(text: str) -> list[str]:
    """TRON and `0x` addresses in free text whose checksum holds (see `_scan`)."""
    return _scan(text)[0]


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


# --- NBCTF: seizure orders, imported from the owner's downloads (D-085, VS-20) --------------------

MAX_FILE_BYTES = 50 * 1024 * 1024  # the official export is about 0.4 MB; refuse anything absurd
#: Columns of the official export (matal.mod.gov.il, seizure orders, VS-20): one row per order,
#: its wallets in `Assets`. The export's other lists (organisations, operatives) hold no wallets.
OFFICIAL_COLUMNS = ("Name en", "Order Type", "Is Canceled", "Assets")


@dataclass(frozen=True)
class OrderImport:
    order: str  # the order's name ("FO 43/25"), or the file's for a file that is one order
    addresses: tuple[str, ...]  # valid, in order
    rejected: tuple[str, ...]  # TRON-looking strings whose checksum fails
    program: str = "NBCTF seizure order"  # shown in the finding: the export's type and dates
    valid_to: str | None = None  # the export's Validity Date, ISO (Q-38)
    cancelled: bool = False  # cancelled on the official list: its addresses are not listed

    def lapsed(self, today: date) -> bool:
        """Its validity date has passed. It stays listed while NBCTF publishes it (Q-38)."""
        try:
            return self.valid_to is not None and date.fromisoformat(self.valid_to) < today
        except ValueError:
            return False


@dataclass(frozen=True)
class FileImport:
    name: str
    orders: tuple[OrderImport, ...]
    official: bool  # rows of the official export, not one order per file


@dataclass(frozen=True)
class NbctfImport:
    files: tuple[FileImport, ...]
    result: SyncResult
    total: int  # different addresses in the new snapshot


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")  # any bytes; addresses are ASCII


def _official_orders(text: str) -> list[OrderImport] | None:
    """The orders in a CSV of the official export, or None for any other text."""
    header = next(csv.reader([text.split("\n", 1)[0]]), [])
    if not set(OFFICIAL_COLUMNS) <= {h.strip() for h in header}:
        return None
    # A cell can be long (FO 56/23 names 582 people); the file's size is bounded already.
    limit = csv.field_size_limit(MAX_FILE_BYTES)
    try:
        rows = list(csv.DictReader(io.StringIO(text)))
    finally:
        csv.field_size_limit(limit)
    orders = []
    for row in rows:
        cell = {k.strip(): (v or "").strip() for k, v in row.items() if isinstance(k, str)}
        name = cell.get("Name en") or cell.get("Name he") or cell.get("Order Number")
        if not name:
            continue
        valid, failed = _scan(cell.get("Assets", ""))
        program = cell.get("Order Type") or "order"
        if date := cell.get("Order Date"):
            program += f" of {date}"
        if valid_to := cell.get("Validity Date"):
            program += f", valid to {valid_to}"
        cancelled = cell.get("Is Canceled", "").lower() == "true"
        orders.append(
            OrderImport(name, tuple(valid), tuple(failed), program, valid_to or None, cancelled)
        )
    return orders


def read_file(path: Path) -> FileImport:
    """The orders in one file. The official export (its CSV, or its zip of CSVs) gives one order
    per row; any other file (a spreadsheet, a CSV, a text) is one order named after the file, its
    addresses taken from any cell (VS-20)."""
    data = path.read_bytes()
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"{path.name}: larger than {MAX_FILE_BYTES // 2**20} MB")
    if zipfile.is_zipfile(io.BytesIO(data)):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            texts = [
                _decode(z.read(i))
                for i in sorted(z.infolist(), key=lambda i: i.filename)
                if i.filename.lower().endswith((".xml", ".csv")) and i.file_size <= MAX_FILE_BYTES
            ]
    else:
        texts = [_decode(data)]
    official: list[OrderImport] = []
    rest: list[str] = []
    for text in texts:
        orders = _official_orders(text)
        if orders is None:
            rest.append(text)
        else:
            official.extend(orders)
    valid, failed = _scan("\n".join(rest))
    whole = [OrderImport(path.stem, tuple(valid), tuple(failed))]
    return FileImport(
        path.name,
        tuple(official + (whole if valid or failed or not official else [])),
        bool(official),
    )


def import_nbctf(
    conn: sqlite3.Connection, paths: Sequence[Path], now: datetime, *, replace: bool = False
) -> NbctfImport:
    """A new `nbctf` snapshot from the given files. Unless `replace`, the last snapshot's orders
    are kept; an order the files bring again with addresses, or cancelled, is replaced: its newest
    import wins. An order with no wallet in the files leaves what was listed for it alone."""
    files = tuple(read_file(p) for p in sorted(paths))
    orders = [o for f in files for o in f.orders]
    renewed = {o.order for o in orders if o.addresses or o.cancelled}
    rows: dict[tuple[str, str], ListedAddress] = {}
    if not replace:
        for a, entry, name, program in conn.execute(
            "SELECT address_norm, list_entry_id, entity_name, program FROM sanctioned_addresses "
            "WHERE snapshot_id = (SELECT max(id) FROM list_snapshots WHERE source = ?)",
            (NBCTF.source,),
        ):
            if entry not in renewed:
                rows[(a, entry)] = next(_listed([a], entry, name, program))
    for o in orders:
        if not o.cancelled:
            for listed in _listed(o.addresses, o.order, f"order {o.order}", o.program):
                rows[(listed.address_norm, listed.entry_id)] = listed
    ordered = tuple(rows[k] for k in sorted(rows))
    entries = len({a.entry_id for a in ordered})
    digest_input = "\n".join(f"{a.entry_id} {a.address_norm} {a.program}" for a in ordered)
    parsed = ParsedList(now.date().isoformat(), entries, ordered)
    result = store(conn, digest_input.encode(), parsed, Decimal(0), now, source=NBCTF.source)
    return NbctfImport(files, result, len({a.address_norm for a in ordered}))
