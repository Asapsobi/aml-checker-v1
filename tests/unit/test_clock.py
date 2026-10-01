from datetime import UTC, datetime, timedelta, timezone

import pytest

from amlcheck.core.clock import ensure_utc, fixed, from_iso, from_ms, to_iso, utcnow


def test_utcnow_is_aware_utc() -> None:
    assert utcnow().utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    "dt",
    [
        datetime(2026, 10, 1, 10, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 1, 10, 0, 0, 123456, tzinfo=UTC),
        datetime(2020, 2, 29, 23, 59, 59, tzinfo=UTC),
    ],
)
def test_iso_round_trip(dt: datetime) -> None:
    assert from_iso(to_iso(dt)) == dt


def test_to_iso_uses_z_suffix() -> None:
    assert to_iso(datetime(2026, 10, 1, 10, tzinfo=UTC)) == "2026-10-01T10:00:00Z"


def test_to_iso_converts_offsets_to_utc() -> None:
    plus2 = datetime(2026, 10, 1, 12, tzinfo=timezone(timedelta(hours=2)))
    assert to_iso(plus2) == "2026-10-01T10:00:00Z"


def test_from_iso_accepts_offsets() -> None:
    assert from_iso("2026-10-01T12:00:00+02:00") == datetime(2026, 10, 1, 10, tzinfo=UTC)


def test_naive_refused() -> None:
    with pytest.raises(ValueError, match="naive"):
        from_iso("2026-10-01T10:00:00")
    with pytest.raises(ValueError, match="naive"):
        ensure_utc(datetime(2026, 10, 1))  # noqa: DTZ001


def test_from_ms() -> None:
    assert from_ms(1_790_850_000_123) == datetime(2026, 10, 1, 10, 20, 0, 123000, tzinfo=UTC)
    assert to_iso(from_ms(0)) == "1970-01-01T00:00:00Z"


def test_fixed_clock() -> None:
    at = datetime(2026, 10, 1, tzinfo=UTC)
    clock = fixed(at)
    assert clock() == at
    assert clock() == at


def test_to_db_is_fixed_width_and_sorts_as_time() -> None:
    from amlcheck.core.clock import to_db

    whole = datetime(2026, 10, 1, 10, 0, 0, tzinfo=UTC)
    half = datetime(2026, 10, 1, 10, 0, 0, 500000, tzinfo=UTC)
    assert to_db(whole) == "2026-10-01T10:00:00.000000Z"
    assert to_db(whole) < to_db(half)
    assert to_iso(whole) > to_iso(half)  # why to_iso must not be used for ordering
    assert from_iso(to_db(half)) == half
