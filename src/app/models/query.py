"""Sort and filter options, validated at the boundary.

The column names that reach SQL come only from a model's generated allow-list,
never from a request. Filter *values* are always bound as `?` parameters and
are never touched here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


class InvalidQuery(Exception):
    """A sort or filter named a column outside the model's whitelist.

    Handlers map this to HTTP 422.
    """


@dataclass(frozen=True, slots=True)
class QueryOpts:
    """Empty fields mean "not requested"."""

    sort: str = ""
    filter_field: str = ""
    filter_value: str = ""


def order_by_clause(sort: str, allowed: Sequence[str]) -> str:
    """Build a safe ORDER BY for a sort spec. A leading '-' means DESC.

    The returned column is always a member of `allowed` — a generated literal
    of the model's real columns — which is what makes interpolating it into SQL
    safe. Anything else raises InvalidQuery.
    """
    if not sort:
        return "ORDER BY created_at DESC"
    column, direction = sort, "ASC"
    if sort.startswith("-"):
        column, direction = sort[1:], "DESC"
    if column not in allowed:
        raise InvalidQuery(column)
    return f"ORDER BY {column} {direction}"


def filter_field(field: str, allowed: Sequence[str]) -> str:
    """Validate a filter column against the whitelist and return it."""
    if field not in allowed:
        raise InvalidQuery(field)
    return field
