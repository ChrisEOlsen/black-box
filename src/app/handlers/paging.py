"""The list window, read straight off the query string.

Out-of-range values are CLAMPED, not rejected — that is a contract guarantee
(docs/API-CONTRACT.md § Pagination), and it is why these are not declared as
FastAPI `Query(ge=..., le=...)` params: those answer 422 instead of clamping.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request

DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200
MIN_PAGE_LIMIT = 1
MAX_PAGE_OFFSET = 1_000_000


def query_int(request: Request, name: str, default: int, low: int, high: int) -> int:
    """Read one integer query param, clamped into [low, high].

    A missing or unparseable value yields the default, matching the contract's
    "clamped, not rejected" rule for anything a client can get wrong.
    """
    raw = request.query_params.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(low, min(high, value))


@dataclass(frozen=True, slots=True)
class PageWindow:
    limit: int
    offset: int


def page_window(request: Request) -> PageWindow:
    return PageWindow(
        limit=query_int(request, "limit", DEFAULT_PAGE_LIMIT, MIN_PAGE_LIMIT, MAX_PAGE_LIMIT),
        offset=query_int(request, "offset", 0, 0, MAX_PAGE_OFFSET),
    )


PageDep = Annotated[PageWindow, Depends(page_window)]
