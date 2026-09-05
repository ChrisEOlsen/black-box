# Code generated from api.json by bb-builder. DO NOT EDIT.
"""Every registered page must actually serve its shell.

Generated rather than hand-written because the assertions are per-page: a
hand-written test cannot know which pages a project scaffolded, and a page that
is registered but unreachable is exactly the defect this closes.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cache.cache import Cache
from db.testutil import open_test
from handlers.envelope import register_exception_handlers
from handlers.pages_gen import register_pages


@pytest.fixture
def client() -> Iterator[TestClient]:
    # A database is on the state even when no page is guarded: require_page_auth
    # resolves the session through it, and a bare app would fail with an
    # AttributeError rather than a redirect.
    with open_test() as db:
        app = FastAPI()
        register_exception_handlers(app)
        app.state.database = db
        app.state.cache = Cache(janitor=False)
        register_pages(app)
        yield TestClient(app)


@pytest.mark.parametrize("path", ["/login", "/register"])
def test_page_serves_its_shell(client: TestClient, path: str) -> None:
    res = client.get(path)
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/html")
