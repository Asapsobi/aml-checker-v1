"""Injectable UTC clock and ISO-8601 helpers.

Code never calls `datetime.now()` directly: time comes from a `Clock` passed in, so tests and
replays are deterministic (CLAUDE.md non-negotiable 6). Storage uses ISO-8601 UTC text
(data model intro).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

type Clock = Callable[[], datetime]


def utcnow() -> datetime:
    """The real clock: timezone-aware UTC, microseconds kept."""
    return datetime.now(UTC)


def fixed(at: datetime) -> Clock:
    """A clock that always returns `at`. For tests and replays."""
    at = ensure_utc(at)
    return lambda: at


def ensure_utc(dt: datetime) -> datetime:
    """Refuse naive datetimes; convert aware ones to UTC."""
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(f"naive datetime not allowed: {dt!r}")
    return dt.astimezone(UTC)


def to_iso(dt: datetime) -> str:
    """UTC ISO-8601 with a `Z` suffix, e.g. `2026-10-01T10:00:00Z` or `…T10:00:00.123456Z`."""
    return ensure_utc(dt).isoformat().replace("+00:00", "Z")


def from_iso(text: str) -> datetime:
    """Parse ISO-8601 with `Z` or an explicit offset. Naive input is refused, not assumed UTC."""
    return ensure_utc(datetime.fromisoformat(text))


def from_ms(ms: int) -> datetime:
    """Provider timestamps in milliseconds since the epoch (e.g. TronGrid `block_timestamp`)."""
    return datetime.fromtimestamp(ms / 1000, UTC)
