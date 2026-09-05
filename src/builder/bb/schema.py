"""Validating a field declaration against the real table.

The declaration stays a statement of intent; the table is the source of truth.
A mismatch fails the command with a diff rather than silently generating a
model that lies about the data.
"""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass

from bb.fields import SEMANTIC_FORMATS, Field, is_safe_ident, validate_field_types

# Names that would collide with hand-written code in the models package.
# `timestamp`, `query`, `user` and `mobile_token` are modules that already
# exist there; the rest would shadow a module a generated model imports.
#
# Python needs a longer list than Go did: Go's collision would be a compile
# error naming the file, while a Python module shadowing `models/query.py`
# breaks every generated GetPage at import time, far from the cause.
# See docs/DECISIONS.md § 12.
RESERVED_MODEL_NAMES = frozenset(
    {
        "timestamp",
        "query",
        "user",
        "mobile_token",
        "cache",
        "db",
        "database",
        "main",
        "conftest",
    }
)

# Anything in the standard library would shadow a real import from inside
# models/, which is a plain package directory on sys.path.
_STDLIB_NAMES = sys.stdlib_module_names


class SchemaError(Exception):
    """A table or field declaration the tools refuse."""


def check_reserved_name(name: str) -> None:
    lowered = name.lower()
    if lowered in RESERVED_MODEL_NAMES:
        raise SchemaError(
            f"model name {name!r} is reserved by the template — it already exists "
            "in the models package"
        )
    if lowered in _STDLIB_NAMES:
        raise SchemaError(
            f"model name {name!r} would shadow the standard library module of the same "
            "name from inside models/ — pick another name"
        )


def credential_column(name: str) -> bool:
    """Report a column that stores a secret.

    The scaffolding tools build generic CRUD, where a PUT that merely omits a
    field writes its default — so a credential column reachable from one is a
    credential anybody can blank. Authentication ships as hand-written code in
    src/app; nothing generated has any business holding a secret.
    """
    lowered = name.lower()
    # A stored digest, however it is spelled: password_hash, token_hash.
    if lowered.endswith("hash"):
        return True
    if any(word in lowered for word in ("password", "passwd", "passphrase")):
        return True
    # Matched per underscore-separated segment, not as a substring, so
    # `secretary_id` and `salted_caramel` stay ordinary columns while
    # `client_secret` does not.
    return any(seg in {"secret", "otp", "salt", "apikey", "token"} for seg in lowered.split("_"))


def check_no_credential_columns(fields: list[Field]) -> None:
    for f in fields:
        if credential_column(f.name):
            raise SchemaError(
                f"field {f.name!r} looks like a credential — the scaffolding tools generate "
                "generic CRUD, where a request that omits the field would overwrite it. "
                "Authentication already ships in src/app/handlers/auth.py; keep secrets there"
            )


@dataclass(frozen=True, slots=True)
class Column:
    name: str
    sql_type: str
    not_null: bool


def normalize_sql_type(declared: str) -> str:
    """Strip length qualifiers and casing, so VARCHAR(255) and varchar both
    compare equal to TEXT's affinity family."""
    text = declared.strip().upper()
    if "(" in text:
        text = text[: text.index("(")]
    if "BOOL" in text or "INT" in text:
        return "INTEGER"
    if any(token in text for token in ("CHAR", "TEXT", "CLOB")):
        return "TEXT"
    if any(token in text for token in ("REAL", "FLOA", "DOUB")):
        return "REAL"
    return text


def accepted_sql_types(field_type: str) -> list[str]:
    """The normalized column types a declared field type may legitimately sit on.

    A list rather than one value because of `timestamp`: SQLite has no date
    type, so a DATETIME column is a convention, and the same column is spelled
    DATETIME by one author and TEXT by another. Both store identical bytes and
    both parse into Timestamp, so refusing one would be pedantry that pushes
    the author back to declaring the field a `string` — the defect this field
    type exists to remove.
    """
    if field_type in ("int", "boolean"):
        return ["INTEGER"]
    if field_type == "float":
        return ["REAL"]
    if field_type == "timestamp":
        return ["DATETIME", "TEXT", "DATE", "TIMESTAMP"]
    return ["TEXT"]


def table_columns(dsn: str, table: str) -> list[Column]:
    """Read a table's shape from SQLite's schema.

    PRAGMA does not accept bound parameters, so the table name is
    interpolated. The is_safe_ident check is what makes that safe — it must
    stay.
    """
    if not is_safe_ident(table):
        raise SchemaError(f"unsafe table name {table!r}")
    conn = sqlite3.connect(dsn, uri=dsn.startswith("file:"))
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    finally:
        conn.close()
    return [Column(name=r[1], sql_type=normalize_sql_type(r[2]), not_null=r[3] == 1) for r in rows]


def require_implicit_columns(table: str, columns: list[Column]) -> None:
    """Check the two columns every generated model uses without the caller
    declaring them.

    `model.py.j2` hard-codes `id` and `created_at` in the row model, the sort
    whitelist and the SELECT, and lists default to ORDER BY created_at DESC.
    Checked here so a missing column fails the command rather than the first
    request.
    """
    by_name = {c.name: c for c in columns}

    identifier = by_name.get("id")
    if identifier is None:
        raise SchemaError(
            f'table {table!r} has no "id" column — every generated model selects it; '
            "declare it as `id INTEGER PRIMARY KEY`"
        )
    if identifier.sql_type != "INTEGER":
        raise SchemaError(
            f'table {table!r} column "id" is {identifier.sql_type} but generated models '
            "read it as an int — declare it as `id INTEGER PRIMARY KEY`"
        )

    created = by_name.get("created_at")
    if created is None:
        raise SchemaError(
            f'table {table!r} has no "created_at" column — every generated model selects it '
            "and lists default to `ORDER BY created_at DESC`; declare it as "
            "`created_at DATETIME DEFAULT CURRENT_TIMESTAMP`"
        )
    if created.sql_type not in accepted_sql_types("timestamp"):
        raise SchemaError(
            f'table {table!r} column "created_at" is {created.sql_type} but generated models '
            "parse it as a Timestamp — declare it as "
            "`created_at DATETIME DEFAULT CURRENT_TIMESTAMP`"
        )


def describe_type_mismatch(table: str, f: Field, column: Column, accepted: list[str]) -> str:
    """Explain a declared/actual type mismatch in the caller's own words.

    A semantic hint like `due_at:datetime` resolves to a string with a format,
    so naming only the resolved type ("declared as string") describes something
    the author never typed. The hint is named back to them, along with the two
    ways out.
    """
    declared = f.type
    detail = ""
    if f.format:
        hint = next(
            (name for name, mapped in SEMANTIC_FORMATS.items() if mapped == f.format), f.format
        )
        declared = hint
        detail = (
            f" — {hint} is a TEXT column carrying a format hint, which is not the same as "
            f"timestamp. Either declare the column TEXT, or use {f.name}:timestamp to keep "
            "the DATETIME column."
        )
    return (
        f"field {f.name!r} declared as {declared} (expects a "
        f"{' or '.join(accepted)} column) but column {table}.{f.name} is "
        f"{column.sql_type}{detail}"
    )


def apply_schema(dsn: str, table: str, fields: list[Field]) -> list[Field]:
    """Validate declared fields against the real table and fill in nullability."""
    columns = table_columns(dsn, table)
    if not columns:
        raise SchemaError(
            f"table {table!r} does not exist — run `bb sql` to create it before scaffolding"
        )

    require_implicit_columns(table, columns)
    validate_field_types(fields)
    check_no_credential_columns(fields)

    by_name = {c.name: c for c in columns}
    out: list[Field] = []
    for f in fields:
        column = by_name.get(f.name)
        if column is None:
            raise SchemaError(
                f"field {f.name!r} is not a column of table {table!r} "
                f"(columns: {', '.join(c.name for c in columns)})"
            )
        accepted = accepted_sql_types(f.type)
        if column.sql_type not in accepted:
            raise SchemaError(describe_type_mismatch(table, f, column, accepted))
        out.append(
            Field(
                name=f.name, type=f.type, nullable=not column.not_null, format=f.format, ref=f.ref
            )
        )
    return out
