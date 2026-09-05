"""The wire format for a moment in time.

Pydantic v2 serializes `datetime` as ISO 8601 with microseconds and a `+00:00`
offset. Swift's `.iso8601` decoding strategy **rejects fractional seconds**, so
a default-serialized timestamp parses fine in JavaScript and fails on iOS — a
wire defect only the second client ever finds.

`Timestamp` pins the format to RFC3339, UTC, second precision. A model field
holding a moment must be declared `Timestamp`, never a bare `datetime`, and a
DATETIME column is declared `timestamp` at scaffold time, never `string`.

See docs/API-CONTRACT.md § Data rules and docs/DECISIONS.md § 4.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import BeforeValidator, PlainSerializer

# Every shape SQLite can hand back for a DATETIME column. Python's sqlite3
# datetime adapters are deprecated in 3.12+ and deliberately not registered, so
# the column arrives as a string and is parsed here.
_SCAN_LAYOUTS = (
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f%z",
    "%Y-%m-%d %H:%M:%S%z",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
)

WIRE_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def parse_timestamp(value: Any) -> Any:
    """Coerce whatever the driver or the wire produced into an aware datetime.

    Non-string, non-datetime values pass through untouched so Pydantic reports
    the type error itself, with its own message and field path.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str):
        return value

    text = value.strip()
    if not text:
        return value
    # A trailing Z is RFC3339 but not accepted by %z on every platform.
    normalized = text[:-1] + "+0000" if text.endswith("Z") else text
    for layout in _SCAN_LAYOUTS:
        try:
            parsed = datetime.strptime(normalized, layout)
        except ValueError:
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return value


def format_timestamp(value: datetime) -> str:
    """Render as RFC3339 in UTC with no fractional part."""
    aware = value if value.tzinfo else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).strftime(WIRE_FORMAT)


def timestamp_to_db(value: datetime | None) -> str | None:
    """Render for binding into SQLite.

    Python's sqlite3 datetime adapters are deprecated since 3.12 and are not
    registered, so binding a datetime directly raises. Generated models bind
    every timestamp column through this instead, which also guarantees the
    stored text is the same RFC3339 the wire uses.
    """
    return None if value is None else format_timestamp(value)


Timestamp = Annotated[
    datetime,
    BeforeValidator(parse_timestamp),
    PlainSerializer(format_timestamp, return_type=str),
]
