from __future__ import annotations

import time

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.responses import Response

from middleware.session import SESSION_COOKIE_NAME, clear_session, read_session, set_session


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()

    @app.post("/issue")
    def issue(response: Response) -> dict[str, bool]:
        set_session(response, 7, 3)
        return {"ok": True}

    @app.get("/read")
    def read(request: Request) -> dict[str, int | None]:
        payload = read_session(request)
        return {"uid": payload.user_id if payload else None}

    @app.post("/clear")
    def clear(response: Response) -> dict[str, bool]:
        clear_session(response)
        return {"ok": True}

    return TestClient(app)


def uid(client: TestClient) -> int | None:
    value: int | None = client.get("/read").json()["uid"]
    return value


def test_round_trips_a_signed_session(client: TestClient) -> None:
    client.post("/issue")
    assert uid(client) == 7


def test_a_tampered_payload_is_rejected(client: TestClient) -> None:
    client.post("/issue")
    payload, _, signature = client.cookies[SESSION_COOKIE_NAME].partition("|")
    client.cookies.set(SESSION_COOKIE_NAME, f"{payload}x|{signature}")
    assert uid(client) is None


def test_a_forged_signature_is_rejected(client: TestClient) -> None:
    client.post("/issue")
    payload, _, _ = client.cookies[SESSION_COOKIE_NAME].partition("|")
    client.cookies.set(SESSION_COOKIE_NAME, f"{payload}|forged")
    assert uid(client) is None


def test_a_cookie_signed_with_another_secret_is_rejected(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client.post("/issue")
    monkeypatch.setenv("SESSION_SECRET", "a-completely-different-secret-0123456789ab")
    assert uid(client) is None


def test_an_expired_cookie_is_rejected(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    client.post("/issue")
    # Captured before patching, or the replacement recurses into itself.
    later = time.time() + 90_000
    monkeypatch.setattr("middleware.session.time.time", lambda: later)
    assert uid(client) is None


@pytest.mark.parametrize("value", ["", "nopipe", "|", "a|b", "!!!|???"])
def test_garbage_is_rejected_without_raising(client: TestClient, value: str) -> None:
    client.cookies.set(SESSION_COOKIE_NAME, value)
    assert uid(client) is None


def test_clear_removes_the_cookie(client: TestClient) -> None:
    client.post("/issue")
    client.post("/clear")
    assert uid(client) is None


def test_a_short_secret_is_refused(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A short key is the difference between a session nobody can forge and one
    anybody can, so it fails loudly rather than signing weakly."""
    monkeypatch.setenv("SESSION_SECRET", "too-short")
    with pytest.raises(RuntimeError, match="at least 32"):
        client.post("/issue")
