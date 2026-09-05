"""Contract tests for the response envelope.

Every failure path FastAPI can produce on its own is exercised here, because
each one answers `{"detail": ...}` by default and a regression would be visible
to clients but invisible to a route's own tests.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from handlers.envelope import (
    ApiError,
    Envelope,
    Meta,
    code_for_status,
    normalize_data,
    not_found,
    register_exception_handlers,
    summarize_fields,
    validation_failed,
)


class Item(BaseModel):
    id: int
    name: str


class Body(BaseModel):
    name: str
    quantity: int


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/api/v1/items", response_model=Envelope[list[Item]])
    def list_items() -> Envelope[list[Item]]:
        return Envelope(data=[Item(id=1, name="a")], meta=Meta(limit=50, offset=0, total=1))

    @app.get("/api/v1/items/{item_id}", response_model=Envelope[Item])
    def get_item(item_id: int) -> Envelope[Item]:
        if item_id != 1:
            raise not_found()
        return Envelope(data=Item(id=1, name="a"))

    @app.post("/api/v1/items", response_model=Envelope[Item])
    def create_item(body: Body) -> Envelope[Item]:
        return Envelope(data=Item(id=2, name=body.name))

    @app.get("/api/v1/boom")
    def boom() -> None:
        raise RuntimeError("secret value 12345")

    @app.get("/api/v1/fields")
    def fields() -> None:
        raise validation_failed({"name": "required", "email": "invalid"})

    @app.get("/page")
    def page() -> str:
        return "hi"

    return TestClient(app, raise_server_exceptions=False)


# --- success shapes ------------------------------------------------------


def test_success_omits_error_members(client: TestClient) -> None:
    body = client.get("/api/v1/items/1").json()
    assert body == {"ok": True, "data": {"id": 1, "name": "a"}}


def test_list_carries_meta(client: TestClient) -> None:
    body = client.get("/api/v1/items").json()
    assert body["ok"] is True
    assert body["meta"] == {"limit": 50, "offset": 0, "total": 1}
    assert isinstance(body["data"], list)


def test_empty_list_is_a_list_not_null() -> None:
    """A strict decoder binding an array fails on null."""
    envelope: Envelope[list[Item]] = Envelope(data=[])
    assert envelope.model_dump()["data"] == []


# --- the four failure paths ---------------------------------------------


def test_raised_api_error_is_enveloped(client: TestClient) -> None:
    res = client.get("/api/v1/items/99")
    assert res.status_code == 404
    assert res.json() == {"ok": False, "error": "not found", "code": "not_found"}


def test_body_validation_failure_becomes_a_field_map(client: TestClient) -> None:
    res = client.post("/api/v1/items", json={"quantity": "abc"})
    assert res.status_code == 422
    body = res.json()
    assert body["ok"] is False
    assert body["code"] == "validation_failed"
    assert set(body["fields"]) == {"name", "quantity"}
    # The message is the alphabetically first field, so it is deterministic.
    assert body["error"].startswith("name:")


def test_unmatched_api_path_is_enveloped(client: TestClient) -> None:
    res = client.get("/api/v1/nope")
    assert res.status_code == 404
    assert res.json() == {"ok": False, "error": "Not Found", "code": "not_found"}


def test_wrong_method_is_enveloped(client: TestClient) -> None:
    res = client.delete("/api/v1/items")
    assert res.status_code == 405
    assert res.json()["code"] == "method_not_allowed"


def test_unhandled_exception_is_enveloped_and_leaks_nothing(client: TestClient) -> None:
    res = client.get("/api/v1/boom")
    assert res.status_code == 500
    assert res.json() == {"ok": False, "error": "internal error", "code": "internal"}
    assert "12345" not in res.text


def test_explicit_field_map_survives_to_the_wire(client: TestClient) -> None:
    body = client.get("/api/v1/fields").json()
    assert body["fields"] == {"name": "required", "email": "invalid"}


# --- the human-facing namespace is not enveloped -------------------------


def test_non_api_path_keeps_the_browser_error(client: TestClient) -> None:
    res = client.get("/does-not-exist")
    assert res.status_code == 404
    assert res.headers["content-type"].startswith("text/plain")


# --- unit level ----------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, "unauthorized"),
        (403, "forbidden"),
        (404, "not_found"),
        (405, "method_not_allowed"),
        (409, "conflict"),
        (422, "validation_failed"),
        (429, "rate_limited"),
        (503, "unavailable"),
        (418, "validation_failed"),  # unenumerated 4xx is about the request
        (500, "internal"),
        (502, "internal"),
    ],
)
def test_code_for_status(status: int, code: str) -> None:
    assert code_for_status(status) == code


def test_api_error_derives_its_code_from_the_status() -> None:
    assert ApiError(409, "taken").code == "conflict"
    assert ApiError(409, "taken", code="custom").code == "custom"


def test_summarize_fields_is_deterministic() -> None:
    assert summarize_fields({"z": "bad", "a": "worse"}) == "a: worse"
    assert summarize_fields({}) == "validation failed"


def test_normalize_data_replaces_none_with_empty_list() -> None:
    assert normalize_data(None) == []
    assert normalize_data([1]) == [1]
