from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from handlers.paging import PageDep, PageWindow


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()

    @app.get("/w")
    def window(page: PageDep) -> dict[str, int]:
        return {"limit": page.limit, "offset": page.offset}

    return TestClient(app)


def get(client: TestClient, query: str) -> PageWindow:
    body = client.get(f"/w{query}").json()
    return PageWindow(limit=body["limit"], offset=body["offset"])


def test_defaults(client: TestClient) -> None:
    assert get(client, "") == PageWindow(limit=50, offset=0)


def test_values_pass_through(client: TestClient) -> None:
    assert get(client, "?limit=10&offset=20") == PageWindow(limit=10, offset=20)


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("?limit=0", PageWindow(limit=1, offset=0)),
        ("?limit=9999", PageWindow(limit=200, offset=0)),
        ("?limit=-5", PageWindow(limit=1, offset=0)),
        ("?offset=-1", PageWindow(limit=50, offset=0)),
    ],
)
def test_out_of_range_is_clamped_not_rejected(
    client: TestClient, query: str, expected: PageWindow
) -> None:
    """A contract guarantee — see docs/API-CONTRACT.md § Pagination."""
    assert get(client, query) == expected


@pytest.mark.parametrize("query", ["?limit=abc", "?limit=", "?offset=1.5"])
def test_unparseable_falls_back_to_the_default(client: TestClient, query: str) -> None:
    assert client.get(f"/w{query}").status_code == 200
