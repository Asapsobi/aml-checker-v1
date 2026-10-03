"""Audit export CSV/JSON/PDF, CSV-injection safe (PRD F11, AT-47).

A spreadsheet runs a cell that starts with `=`, `+`, `-`, `@`, a tab or a carriage return as a
formula. Addresses, notes and client names come from outside, so every CSV cell we write goes
through `csv_cell`, which puts a `'` in front of such a value (OWASP CSV injection).
"""

from __future__ import annotations

_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def csv_cell(value: object) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(_FORMULA_START) else text
