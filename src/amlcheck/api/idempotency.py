"""`Idempotency-Key` (IETF draft "The Idempotency-Key HTTP Header Field", PRD F14.3).

- The first request with a key is recorded with a fingerprint of the request as understood
  (normalised fields, sha256) before any work starts.
- A repeat with the same key and fingerprint gets the original result (the same check or trace).
- The same key with a different request → 422; a repeat while the first is still running → 409.
- A key whose request died before finishing (a crash) is released after `STALE`, for a retry.
Keys never expire once finished, like the records they point to.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from amlcheck.core.clock import from_iso, to_db
from amlcheck.storage.db import transaction

PENDING = ""  # ref_id while the first request is running
STALE = timedelta(minutes=15)  # longer than any check with its trace (time budget + margin)
MAX_KEY = 255


class KeyMismatch(Exception):
    """Same key, different request (422)."""


class InProgress(Exception):
    """The first request with this key is still running (409)."""


@dataclass(frozen=True)
class Known:
    ref_id: str


def fingerprint(kind: str, request: dict[str, Any]) -> str:
    text = json.dumps({"kind": kind, **request}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def begin(conn: sqlite3.Connection, key: str, kind: str, fp: str, now: datetime) -> Known | None:
    """None when this request should run (the key is now held); Known when it already ran."""
    with transaction(conn):
        row = conn.execute(
            "SELECT fingerprint, kind, ref_id, created_at FROM api_requests "
            "WHERE idempotency_key = ?",
            (key,),
        ).fetchone()
        if row is not None:
            if row[0] != fp or row[1] != kind:
                raise KeyMismatch(key)
            if row[2] != PENDING:
                return Known(row[2])
            if from_iso(row[3]) > now - STALE:
                raise InProgress(key)
            conn.execute(  # the first attempt died: this one takes the key over
                "UPDATE api_requests SET created_at = ? WHERE idempotency_key = ?",
                (to_db(now), key),
            )
            return None
        conn.execute(
            "INSERT INTO api_requests (idempotency_key, fingerprint, kind, ref_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (key, fp, kind, PENDING, to_db(now)),
        )
    return None


def finish(conn: sqlite3.Connection, key: str, ref_id: str) -> None:
    with transaction(conn):
        conn.execute("UPDATE api_requests SET ref_id = ? WHERE idempotency_key = ?", (ref_id, key))


def release(conn: sqlite3.Connection, key: str) -> None:
    """The request failed before producing anything: the key may be used again."""
    with transaction(conn):
        conn.execute(
            "DELETE FROM api_requests WHERE idempotency_key = ? AND ref_id = ?", (key, PENDING)
        )
