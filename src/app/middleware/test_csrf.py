"""CSRF is a boundary, so its edges get their own tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from middleware.csrf import CSRF_COOKIE_NAME, CSRF_HEADER_NAME
from middleware.session import SESSION_COOKIE_NAME


def test_safe_request_mints_the_double_submit_cookie(anon: TestClient) -> None:
    res = anon.get("/api/v1/_version")
    assert res.status_code == 200
    assert CSRF_COOKIE_NAME in res.cookies


def test_unsafe_request_with_a_cookie_and_no_header_is_refused(anon: TestClient) -> None:
    anon.get("/api/v1/_version")  # mint the cookie
    res = anon.post("/api/v1/auth/login", json={"email": "a@b.co", "password": "x"})
    assert res.status_code == 403
    assert res.json() == {"ok": False, "error": "invalid CSRF token", "code": "forbidden"}


def test_unsafe_request_with_a_mismatched_header_is_refused(anon: TestClient) -> None:
    anon.get("/api/v1/_version")
    res = anon.post(
        "/api/v1/auth/login",
        json={"email": "a@b.co", "password": "x"},
        headers={CSRF_HEADER_NAME: "not-the-token"},
    )
    assert res.status_code == 403


def test_cookieless_caller_is_let_through(anon: TestClient) -> None:
    """A native client or webhook holds no ambient credential, so there is
    nothing a forged cross-site request could replay."""
    anon.cookies.clear()
    res = anon.post("/api/v1/auth/login", json={"email": "a@b.co", "password": "x"})
    assert res.status_code != 403


def test_a_session_without_a_csrf_cookie_still_verifies(anon: TestClient) -> None:
    """Fails closed: the token is a fresh random the client cannot know."""
    anon.cookies.set(SESSION_COOKIE_NAME, "anything")
    res = anon.post("/api/v1/auth/logout")
    assert res.status_code == 403


@pytest.mark.parametrize("method", ["PATCH", "PUT", "DELETE"])
def test_unsafe_methods_are_all_verified(anon: TestClient, method: str) -> None:
    """Safe methods are allowlisted, so a method nobody thought of is verified
    by default — PATCH is the one the inverted check used to miss."""
    anon.get("/api/v1/_version")
    res = anon.request(method, "/api/v1/auth/logout_all")
    assert res.status_code == 403


def test_bearer_request_is_exempt(anon: TestClient) -> None:
    anon.get("/api/v1/_version")
    res = anon.delete("/api/v1/auth/logout_token", headers={"Authorization": "Bearer abc"})
    assert res.status_code != 403
