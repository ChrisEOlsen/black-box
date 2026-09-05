"""The `name:type` field DSL, and the type maps every template renders through.

Identical to gova-monolith's, so a command written for one template runs
unchanged on the other.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Every type a field declaration may name. Enforced because an unrecognised
# type would otherwise fall through to `string` — turning a typo like
# `updated_at:timestmap` into exactly the defect the timestamp type exists to
# prevent.
KNOWN_FIELD_TYPES = frozenset({"string", "int", "boolean", "float", "timestamp"})

# A logical type mapped onto a format hint. Each is stored as TEXT and carried
# in Python as a str; only the client-side control differs. The iOS build picks
# its SwiftUI control from the same hint — see docs/API-CONTRACT.md.
SEMANTIC_FORMATS = {
    "datetime": "datetime-local",
    "date": "date",
    "time": "time",
    "json": "json",
    "email": "email",
}

_SAFE_IDENT = re.compile(r"^[a-zA-Z0-9_]+$")


@dataclass(slots=True)
class Field:
    """One column of a model, as declared by a caller and then checked against
    the real table by `apply_schema`."""

    name: str
    type: str
    # Filled in from PRAGMA table_info, never from the caller.
    nullable: bool = False
    # A semantic hint on a string-stored column.
    format: str = ""
    # The target model of a foreign key.
    ref: str = ""


class FieldError(Exception):
    """A field declaration the tools refuse."""


def split_fields(raw: str) -> list[str]:
    """Turn "a:string,b:int" into ["a:string", "b:int"].

    Empty entries are dropped, so a trailing comma is not an error.
    """
    return [part.strip() for part in raw.split(",") if part.strip()]


def parse_fields(raw: list[str]) -> list[Field]:
    """Read the DSL. `name:ref:<model>` is a foreign key; `name:<semantic>` is
    a string with a format hint; `name` alone is a string."""
    fields: list[Field] = []
    for entry in raw:
        parts = entry.split(":")
        name = parts[0]
        if len(parts) >= 3 and parts[1] == "ref":
            fields.append(Field(name=name, type="int", ref=parts[2]))
        elif len(parts) == 2:
            hint = SEMANTIC_FORMATS.get(parts[1])
            if hint:
                fields.append(Field(name=name, type="string", format=hint))
            else:
                fields.append(Field(name=name, type=parts[1]))
        else:
            fields.append(Field(name=name, type="string"))
    return fields


def validate_field_types(fields: list[Field]) -> None:
    for f in fields:
        if f.type not in KNOWN_FIELD_TYPES:
            raise FieldError(
                f"field {f.name!r} has unknown type {f.type!r} — use one of: "
                + ", ".join(sorted(KNOWN_FIELD_TYPES))
            )


def is_safe_ident(value: str) -> bool:
    return bool(_SAFE_IDENT.match(value))


def to_pascal(snake: str) -> str:
    return "".join(part[:1].upper() + part[1:] for part in snake.split("_") if part)


def to_plural(value: str) -> str:
    """Never returns its input unchanged, which is what keeps the resource page
    namespace (/projects) from colliding with the auth pages (/login) without
    needing a reserved-word list. A resource named `login` lands at /logins."""
    if value.endswith("y"):
        return value[:-1] + "ies"
    if value.endswith("s"):
        return value + "es"
    return value + "s"


def title_case(value: str) -> str:
    return " ".join(word[:1].upper() + word[1:] for word in value.replace("_", " ").split())


def python_type(field_type: str) -> str:
    """The annotation a generated model carries.

    `timestamp` becomes Timestamp, which pins the JSON form to RFC3339 without
    fractional seconds. See docs/API-CONTRACT.md.
    """
    return {
        "int": "int",
        "timestamp": "Timestamp",
        "boolean": "bool",
        "float": "float",
    }.get(field_type, "str")


def field_annotation(f: Field) -> str:
    """python_type plus nullability. A nullable column becomes `| None`, which
    serializes to JSON null and maps to a Swift optional."""
    base = python_type(f.type)
    return f"{base} | None" if f.nullable else base


def sql_type(field_type: str) -> str:
    """The column type a generated test fixture declares."""
    return {
        "timestamp": "DATETIME",
        "int": "INTEGER",
        "boolean": "INTEGER",
        "float": "REAL",
    }.get(field_type, "TEXT")


def html_input_type(f: Field) -> str:
    """The form control a field's format (or type) calls for."""
    if f.format and f.format != "json":
        return f.format
    return {
        "int": "number",
        "float": "number",
        "boolean": "checkbox",
        "timestamp": "datetime-local",
    }.get(f.type, "text")


def test_literal(field_type: str) -> str:
    """A sample value for a generated test.

    A timestamp must be RFC3339 or the model refuses to decode it and the
    handler answers 422 — which is correct behavior and a useless test.
    """
    return {
        "int": "1",
        "timestamp": "datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC)",
        "boolean": "True",
        "float": "1.5",
    }.get(field_type, '"test"')


def test_json_literal(field_type: str) -> str:
    """A value for a generated request body.

    Still a PYTHON literal — the template emits a dict that httpx serializes,
    so a boolean is `True`, not JSON's `true`. A timestamp is the RFC3339
    string, because that is what actually crosses the wire.
    """
    return {
        "int": "1",
        "timestamp": '"2024-01-02T03:04:05Z"',
        "boolean": "True",
        "float": "1.5",
    }.get(field_type, '"test"')
