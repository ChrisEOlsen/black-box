"""End-to-end tests for the eight auth endpoints.

The wire shapes asserted here are the contract (docs/API-CONTRACT.md
§ Authentication). A change that breaks one of these breaks the iOS client too.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from middleware.csrf import CSRF_COOKIE_NAME, CSRF_HEADER_NAME
from middleware.session import SESSION_COOKIE_NAME

EMAIL = "ada@example.com"
PASSWORD = "correct-horse-battery"


def csrf_headers(client: TestClient) -> dict[str, str]:
    """api.js reads the double-submit cookie and echoes it as a header."""
    client.get("/api/v1/_version")
    return {CSRF_HEADER_NAME: client.cookies[CSRF_COOKIE_NAME]}


def sign_up(client: TestClient, email: str = EMAIL, password: str = PASSWORD) -> Any:
    return client.post(
        "/api/v1/auth/register",
        json={"name": "Ada", "email": email, "password": password},
        headers=csrf_headers(client),
    )


def sign_in(client: TestClient, email: str = EMAIL, password: str = PASSWORD) -> Any:
    return client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
        headers=csrf_headers(client),
    )


# --- registration --------------------------------------------------------


def test_register_creates_an_account_and_signs_in(client: TestClient) -> None:
    res = sign_up(client)
    assert res.status_code == 200
    assert res.json()["data"] == {"id": 1, "name": "Ada", "email": EMAIL}
    assert SESSION_COOKIE_NAME in res.cookies


def test_register_never_returns_a_password_hash(client: TestClient) -> None:
    assert "hash" not in sign_up(client).text
    assert PASSWORD not in sign_up(client, email="b@example.com").text


@pytest.mark.parametrize(
    ("payload", "bad_field"),
    [
        ({"name": "", "email": EMAIL, "password": PASSWORD}, "name"),
        ({"name": "Ada", "email": "", "password": PASSWORD}, "email"),
        ({"name": "Ada", "email": "nope", "password": PASSWORD}, "email"),
        ({"name": "Ada", "email": EMAIL, "password": "short"}, "password"),
    ],
)
def test_register_validates_its_fields(
    client: TestClient, payload: dict[str, str], bad_field: str
) -> None:
    res = client.post("/api/v1/auth/register", json=payload, headers=csrf_headers(client))
    assert res.status_code == 422
    body = res.json()
    assert body["code"] == "validation_failed"
    assert bad_field in body["fields"]


def test_duplicate_email_is_a_conflict(client: TestClient) -> None:
    sign_up(client)
    res = sign_up(client)
    assert res.status_code == 409
    assert res.json()["code"] == "conflict"


def test_email_is_normalized_on_the_way_in(client: TestClient) -> None:
    sign_up(client, email="Ada@Example.COM ")
    assert sign_in(client, email="ada@example.com").status_code == 200


# --- login ---------------------------------------------------------------


def test_login_returns_the_public_user_and_a_session(client: TestClient) -> None:
    sign_up(client)
    client.cookies.delete(SESSION_COOKIE_NAME)
    res = sign_in(client)
    assert res.status_code == 200
    assert res.json()["data"] == {"id": 1, "name": "Ada", "email": EMAIL}


@pytest.mark.parametrize(
    ("email", "password"),
    [(EMAIL, "wrong-password"), ("nobody@example.com", PASSWORD)],
)
def test_bad_credentials_are_indistinguishable(
    client: TestClient, email: str, password: str
) -> None:
    """A wrong password and an unknown account must answer identically, or the
    endpoint is an account-enumeration oracle."""
    sign_up(client)
    res = sign_in(client, email=email, password=password)
    assert res.status_code == 401
    assert res.json() == {
        "ok": False,
        "error": "Invalid email or password.",
        "code": "unauthorized",
    }


def test_blank_credentials_are_a_field_error(client: TestClient) -> None:
    res = client.post(
        "/api/v1/auth/login", json={"email": "", "password": ""}, headers=csrf_headers(client)
    )
    assert res.status_code == 422
    assert set(res.json()["fields"]) == {"email", "password"}


# --- me / logout ---------------------------------------------------------


def test_me_requires_a_session(anon: TestClient) -> None:
    res = anon.get("/api/v1/auth/me")
    assert res.status_code == 401
    assert res.json()["code"] == "unauthorized"


def test_me_returns_the_signed_in_user(client: TestClient) -> None:
    sign_up(client)
    assert client.get("/api/v1/auth/me").json()["data"]["email"] == EMAIL


def test_logout_clears_this_browsers_session(client: TestClient) -> None:
    sign_up(client)
    res = client.post("/api/v1/auth/logout", headers=csrf_headers(client))
    assert res.json()["data"] == {"status": "logged out"}
    assert client.get("/api/v1/auth/me").status_code == 401


def test_logout_all_retires_a_cookie_on_another_device(client: TestClient) -> None:
    """The epoch bump is what reaches a session this browser cannot touch."""
    sign_up(client)
    other_device = dict(client.cookies)

    res = client.post("/api/v1/auth/logout_all", headers=csrf_headers(client))
    assert res.json()["data"] == {"status": "logged out everywhere"}

    client.cookies.clear()
    for name, value in other_device.items():
        client.cookies.set(name, value)
    assert client.get("/api/v1/auth/me").status_code == 401


def test_logout_all_requires_a_session(anon: TestClient) -> None:
    assert anon.post("/api/v1/auth/logout_all", headers=csrf_headers(anon)).status_code == 401


# --- bearer tokens -------------------------------------------------------


def test_login_token_returns_a_token_and_the_user(client: TestClient) -> None:
    sign_up(client)
    res = client.post("/api/v1/auth/login_token", json={"email": EMAIL, "password": PASSWORD})
    assert res.status_code == 200
    data = res.json()["data"]
    assert len(data["token"]) == 64
    assert data["user"] == {"id": 1, "name": "Ada", "email": EMAIL}


def test_bearer_token_authenticates_me_token(client: TestClient) -> None:
    sign_up(client)
    token = client.post(
        "/api/v1/auth/login_token", json={"email": EMAIL, "password": PASSWORD}
    ).json()["data"]["token"]
    client.cookies.clear()
    res = client.get("/api/v1/auth/me_token", headers={"Authorization": f"Bearer {token}"})
    assert res.json()["data"]["email"] == EMAIL


def test_me_token_rejects_a_bad_token(anon: TestClient) -> None:
    res = anon.get("/api/v1/auth/me_token", headers={"Authorization": "Bearer deadbeef"})
    assert res.status_code == 401


def test_logout_token_revokes_it(client: TestClient) -> None:
    sign_up(client)
    token = client.post(
        "/api/v1/auth/login_token", json={"email": EMAIL, "password": PASSWORD}
    ).json()["data"]["token"]
    client.cookies.clear()
    auth = {"Authorization": f"Bearer {token}"}
    assert client.delete("/api/v1/auth/logout_token", headers=auth).status_code == 200
    assert client.get("/api/v1/auth/me_token", headers=auth).status_code == 401


def test_bearer_login_is_exempt_from_csrf(anon: TestClient) -> None:
    """It is the request that issues the token, so it cannot carry one."""
    sign_up(anon)
    anon.cookies.clear()
    res = anon.post("/api/v1/auth/login_token", json={"email": EMAIL, "password": PASSWORD})
    assert res.status_code == 200
