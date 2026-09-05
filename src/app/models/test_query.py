from __future__ import annotations

import pytest

from models.query import InvalidQuery, filter_field, order_by_clause

ALLOWED = ["id", "name", "status", "created_at"]


def test_empty_sort_defaults_to_newest_first() -> None:
    assert order_by_clause("", ALLOWED) == "ORDER BY created_at DESC"


def test_plain_column_sorts_ascending() -> None:
    assert order_by_clause("name", ALLOWED) == "ORDER BY name ASC"


def test_leading_dash_sorts_descending() -> None:
    assert order_by_clause("-name", ALLOWED) == "ORDER BY name DESC"


@pytest.mark.parametrize(
    "sort",
    ["password_hash", "name; DROP TABLE users", "-name; --", "", "-"],
)
def test_column_outside_the_whitelist_is_rejected(sort: str) -> None:
    """ "" is the one exception — it means "no sort" and yields the default."""
    if sort == "":
        assert order_by_clause(sort, ALLOWED) == "ORDER BY created_at DESC"
        return
    with pytest.raises(InvalidQuery):
        order_by_clause(sort, ALLOWED)


def test_filter_field_accepts_whitelisted_column() -> None:
    assert filter_field("status", ALLOWED) == "status"


def test_filter_field_rejects_anything_else() -> None:
    with pytest.raises(InvalidQuery):
        filter_field("password_hash", ALLOWED)
