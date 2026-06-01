"""Shared datetime normalization for public JSON contracts."""

from datetime import UTC, datetime


def ensure_aware_utc(value: datetime) -> datetime:
    """Return a timezone-aware UTC datetime.

    Legacy timezone-naive values are interpreted as UTC because older clients
    may still submit naive ISO strings and SQLite may round-trip aware values
    without tzinfo.
    """
    if not isinstance(value, datetime):
        raise TypeError("value must be a datetime")
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def serialize_datetime_utc(value: datetime | None) -> str | None:
    """Serialize a datetime with an explicit UTC timezone suffix."""
    if value is None:
        return None
    return ensure_aware_utc(value).isoformat()
