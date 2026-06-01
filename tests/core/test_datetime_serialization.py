from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.core.datetime_serialization import ensure_aware_utc, serialize_datetime_utc


def test_ensure_aware_utc_treats_naive_datetime_as_utc() -> None:
    value = datetime(2026, 5, 31, 18, 0, 0, 123456)

    assert ensure_aware_utc(value) == datetime(
        2026, 5, 31, 18, 0, 0, 123456, tzinfo=UTC
    )


def test_ensure_aware_utc_keeps_utc_datetime() -> None:
    value = datetime(2026, 5, 31, 18, 0, tzinfo=UTC)

    assert ensure_aware_utc(value) == value


def test_ensure_aware_utc_converts_offset_datetime_to_utc() -> None:
    value = datetime(2026, 5, 31, 20, 0, tzinfo=timezone(timedelta(hours=2)))

    assert ensure_aware_utc(value) == datetime(2026, 5, 31, 18, 0, tzinfo=UTC)


def test_serialize_datetime_utc_emits_explicit_timezone_suffix() -> None:
    value = datetime(2026, 5, 31, 18, 0, 0, 123456)

    assert serialize_datetime_utc(value) == "2026-05-31T18:00:00.123456+00:00"


def test_serialize_datetime_utc_preserves_none() -> None:
    assert serialize_datetime_utc(None) is None


def test_ensure_aware_utc_rejects_non_datetime_value() -> None:
    with pytest.raises(TypeError):
        ensure_aware_utc("2026-05-31T18:00:00")  # type: ignore[arg-type]
