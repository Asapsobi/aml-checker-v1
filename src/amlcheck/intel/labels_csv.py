"""labels.csv import: `address,chain,tag,note,source`, replaced whole, all-or-nothing (PRD F5.3).

Every row is checked before anything is written; one bad row means nothing changes and every
problem is reported with its line number (AT-27). Addresses are validated and normalised like a
check's target (a mistyped address must not silently label another one).

Tags in use by the rules: `mixer`, `bridge`, `high_risk` (R-HEU-05, `[heuristics] risky_tags`) and
`allowlist` (excluded from R-HEU-02…04, never from sanctions or freeze findings, F5.4). Any other
lowercase tag is kept as free text.
"""

from __future__ import annotations

import csv
import io
import re
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from amlcheck.core.address import AddressError, detect
from amlcheck.core.models import Chain
from amlcheck.storage.db import transaction

COLUMNS = ("address", "chain", "tag", "note", "source")
_TAG = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,39}$")


class LabelsError(Exception):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Label:
    chain: Chain
    address_norm: str
    tag: str
    note: str | None
    source: str | None


def parse(text: str) -> list[Label]:
    """Validate the whole file; raise `LabelsError` listing every bad row."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    header = [h.strip().lower() for h in (reader.fieldnames or [])]
    if header[: len(COLUMNS)] != list(COLUMNS) or len(header) != len(COLUMNS):
        raise LabelsError([f"header must be exactly: {','.join(COLUMNS)}"])
    problems: list[str] = []
    labels: dict[tuple[Chain, str, str], Label] = {}
    for line, row in enumerate(reader, start=2):
        values = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k is not None}
        if None in row:
            problems.append(f"line {line}: too many fields")
            continue
        if not any(values.values()):
            continue  # blank line
        chain_text = values["chain"].lower()
        chain = None
        if chain_text:
            try:
                chain = Chain(chain_text)
            except ValueError:
                problems.append(f"line {line}: chain must be tron or bsc, not {values['chain']!r}")
                continue
        try:
            addr = detect(values["address"], chain)
        except AddressError as e:
            problems.append(f"line {line}: {e}")
            continue
        tag = values["tag"].lower()
        if not _TAG.match(tag):
            problems.append(
                f"line {line}: tag must be 1–40 of a-z, 0-9, _ or -, not {values['tag']!r}"
            )
            continue
        key = (addr.chain, addr.norm, tag)
        labels.setdefault(
            key, Label(addr.chain, addr.norm, tag, values["note"] or None, values["source"] or None)
        )
    if problems:
        raise LabelsError(problems)
    return [labels[k] for k in sorted(labels, key=lambda k: (k[0].value, k[1], k[2]))]


def replace_all(conn: sqlite3.Connection, labels: Iterable[Label]) -> int:
    rows = [(x.chain.value, x.address_norm, x.tag, x.note, x.source) for x in labels]
    with transaction(conn):
        conn.execute("DELETE FROM labels")
        conn.executemany(
            "INSERT INTO labels (chain, address_norm, tag, note, source) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
    return len(rows)


def tags_for(
    conn: sqlite3.Connection, chain: Chain, addresses: Iterable[str]
) -> Mapping[str, frozenset[str]]:
    """Tags per address, for the given addresses only (local data, no API calls, D-015)."""
    wanted = sorted(set(addresses))
    out: dict[str, set[str]] = {}
    for i in range(0, len(wanted), 500):
        chunk = wanted[i : i + 500]
        marks = ",".join("?" * len(chunk))
        for address, tag in conn.execute(
            f"SELECT address_norm, tag FROM labels WHERE chain = ? AND address_norm IN ({marks})",  # noqa: S608 - only placeholders
            [chain.value, *chunk],
        ):
            out.setdefault(address, set()).add(tag)
    return {a: frozenset(t) for a, t in out.items()}
