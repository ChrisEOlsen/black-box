"""Whole-app assertions: headers, the version handshake, and the lockout."""

from __future__ import annotations

from fastapi.testclient import TestClient

from handlers.ratelimit import MAX_ATTEMPTS_PER_IP
from handlers.test_auth import csrf_headers, sign_up
from middleware.security import CONTENT_SECURITY_POLICY


def test_version_answers_in_the_envelope(anon: TestClient) -> None:
    body = anon.get("/api/v1/_version").json()
    assert body == {
        "ok": True,
        "data": {"api_version": "1.0.0", "min_client_version": "1.0.0"},
    }


def test_security_headers_are_present_on_every_response(anon: TestClient) -> None:
    res = anon.get("/api/v1/_version")
    assert res.headers["Content-Security-Policy"] == CONTENT_SECURITY_POLICY
    assert res.headers["X-Content-Type-Options"] == "nosniff"
    assert res.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert res.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"


def test_csp_forbids_inline_script_and_cdns(anon: TestClient) -> None:
    """The backstop behind the JS rules: an injected script does not execute
    even if it reaches the DOM."""
    policy = anon.get("/api/v1/_version").headers["Content-Security-Policy"]
    assert "script-src 'self'" in policy
    assert "unsafe-inline" not in policy
    assert "object-src 'none'" in policy
    assert "base-uri 'none'" in policy


def test_security_headers_survive_an_error(anon: TestClient) -> None:
    res = anon.get("/api/v1/nope")
    assert res.status_code == 404
    assert "Content-Security-Policy" in res.headers


def test_home_serves_the_shell(anon: TestClient) -> None:
    res = anon.get("/")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/html")


def test_login_locks_out_after_the_address_budget(client: TestClient) -> None:
    sign_up(client)
    for _ in range(MAX_ATTEMPTS_PER_IP):
        res = client.post(
            "/api/v1/auth/login",
            json={"email": "ada@example.com", "password": "wrong"},
            headers=csrf_headers(client),
        )
        assert res.status_code == 401

    res = client.post(
        "/api/v1/auth/login",
        json={"email": "ada@example.com", "password": "wrong"},
        headers=csrf_headers(client),
    )
    assert res.status_code == 429
    assert res.json()["code"] == "rate_limited"


def test_a_successful_login_clears_the_address_bucket(client: TestClient) -> None:
    sign_up(client)
    for _ in range(MAX_ATTEMPTS_PER_IP - 1):
        client.post(
            "/api/v1/auth/login",
            json={"email": "ada@example.com", "password": "wrong"},
            headers=csrf_headers(client),
        )
    good = client.post(
        "/api/v1/auth/login",
        json={"email": "ada@example.com", "password": "correct-horse-battery"},
        headers=csrf_headers(client),
    )
    assert good.status_code == 200
    # The bucket was cleared, so the next wrong guess is a 401, not a 429.
    again = client.post(
        "/api/v1/auth/login",
        json={"email": "ada@example.com", "password": "wrong"},
        headers=csrf_headers(client),
    )
    assert again.status_code == 401


def test_docs_are_mounted_outside_production(anon: TestClient) -> None:
    assert anon.get("/docs").status_code == 200
    assert anon.get("/openapi.json").status_code == 200


def test_swagger_assets_are_served_locally_not_from_a_cdn(anon: TestClient) -> None:
    """The CSP forbids a CDN, so /docs must reference vendored files."""
    html = anon.get("/docs").text
    assert "/static/vendor/swagger/swagger-ui-bundle.js" in html
    assert "cdn.jsdelivr.net" not in html
    assert anon.get("/static/vendor/swagger/swagger-ui.css").status_code == 200
