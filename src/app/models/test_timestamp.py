from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import BaseModel, ValidationError

from models.timestamp import Timestamp


class Row(BaseModel):
    at: Timestamp


class NullableRow(BaseModel):
    at: Timestamp | None


def test_serializes_without_fractional_seconds() -> None:
    """The whole reason this type exists: Swift's .iso8601 rejects them."""
    row = Row(at=datetime(2024, 1, 2, 3, 4, 5, 123456, tzinfo=UTC))
    assert row.model_dump()["at"] == "2024-01-02T03:04:05Z"


def test_serializes_as_utc_with_z_suffix() -> None:
    other = timezone(timedelta(hours=-5))
    row = Row(at=datetime(2024, 1, 2, 8, 4, 5, tzinfo=other))
    assert row.model_dump()["at"] == "2024-01-02T13:04:05Z"


@pytest.mark.parametrize(
    "raw",
    [
        "2024-01-02 03:04:05",  # SQLite CURRENT_TIMESTAMP
        "2024-01-02T03:04:05Z",  # RFC3339, the wire form
        "2024-01-02T03:04:05+00:00",
        "2024-01-02 03:04:05.123456",
        "2024-01-02T03:04:05.123456Z",
    ],
)
def test_parses_every_layout_sqlite_can_produce(raw: str) -> None:
    assert Row(at=raw).model_dump()["at"] == "2024-01-02T03:04:05Z"  # type: ignore[arg-type]


def test_naive_input_is_treated_as_utc() -> None:
    row = Row(at=datetime(2024, 1, 2, 3, 4, 5))
    assert row.model_dump()["at"] == "2024-01-02T03:04:05Z"


def test_round_trips_through_json() -> None:
    row = Row(at=datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC))
    assert Row.model_validate_json(row.model_dump_json()) == row


def test_nullable_field_serializes_as_null() -> None:
    assert NullableRow(at=None).model_dump()["at"] is None


def test_unparseable_string_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Row(at="not a date")  # type: ignore[arg-type]


def test_timestamp_to_db_renders_the_wire_format() -> None:
    """sqlite3's datetime adapters are gone since 3.12, so a bound datetime
    raises — generated models bind through this instead."""
    from models.timestamp import timestamp_to_db

    assert timestamp_to_db(datetime(2024, 1, 2, 3, 4, 5, 999, tzinfo=UTC)) == "2024-01-02T03:04:05Z"
    assert timestamp_to_db(None) is None
