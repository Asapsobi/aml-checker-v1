"""Licensed label packs (PRD F7.4, VS-14).

A pack is a CSV `address,chain,category,note` from a third party. It imports only with a name and a
recorded licence (AT-30); every row is checked first and one bad row imports nothing. Labels get
provenance `import`, `source_ref = pack:<name>` and the licence. Importing a pack again retracts its
earlier labels, so a pack can shrink as well as grow. Before importing, record the licence check in
`docs/verification-log.md` (VS-14 template there).
"""

from __future__ import annotations

import csv
import io
import re
import sqlite3
from dataclasses import dataclass

from amlcheck.core.address import AddressError, detect
from amlcheck.core.models import Chain
from amlcheck.intel.categories import check_assignable
from amlcheck.intel.store import IntelError, IntelStore, NewLabel

COLUMNS = ("address", "chain", "category", "note")
_NAME = re.compile(r"^[a-z0-9][a-z0-9_\-\.]{0,63}$")


@dataclass(frozen=True)
class PackResult:
    added: int
    retracted: int


def parse_pack(text: str) -> list[tuple[Chain, str, str, str | None]]:
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    header = [h.strip().lower() for h in (reader.fieldnames or [])]
    if header != list(COLUMNS):
        raise IntelError(f"pack header must be exactly: {','.join(COLUMNS)}")
    rows: dict[tuple[Chain, str, str], str | None] = {}
    problems: list[str] = []
    for line, row in enumerate(reader, start=2):
        if None in row:
            problems.append(f"line {line}: too many fields")
            continue
        v = {k.strip().lower(): (x or "").strip() for k, x in row.items() if k is not None}
        if not any(v.values()):
            continue
        try:
            chain = Chain(v["chain"].lower()) if v["chain"] else None
            addr = detect(v["address"], chain)
            category = v["category"].lower()
            check_assignable(category, "import")
        except (AddressError, ValueError) as e:
            problems.append(f"line {line}: {e}")
            continue
        rows.setdefault((addr.chain, addr.norm, category), v["note"] or None)
    if problems:
        raise IntelError("; ".join(problems))
    return [
        (c, a, cat, note)
        for (c, a, cat), note in sorted(
            rows.items(), key=lambda kv: (kv[0][0].value, kv[0][1], kv[0][2])
        )
    ]


def import_pack(
    store: IntelStore,
    conn: sqlite3.Connection,
    text: str,
    *,
    name: str,
    licence: str | None,
    by: str | None,
) -> PackResult:
    if not licence or not licence.strip():
        raise IntelError("a label pack imports only with --licence (PRD F7.4)")
    if not _NAME.match(name):
        raise IntelError("pack name: 1-64 of a-z, 0-9, _, - or .")
    rows = parse_pack(text)
    ref = f"pack:{name}"
    old = [
        r[0]
        for r in conn.execute(
            "SELECT id FROM intel_labels WHERE source_ref = ? AND retracted_at IS NULL ORDER BY id",
            (ref,),
        )
    ]
    for label_id in old:
        store.retract(label_id, f"replaced by a new import of {ref}", by)
    for chain, address, category, note in rows:
        store.add_label(
            NewLabel(
                chain,
                address,
                category,
                "import",
                ref,
                note=note,
                licence=licence.strip(),
                created_by=by,
            )
        )
    return PackResult(len(rows), len(old))
