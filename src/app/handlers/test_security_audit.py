"""Regressions for the findings of the 2026-09-06 security audit.

Each test names the finding it closes. They live together because they share
one property: every one of them would otherwise reproduce in every application
generated from this template.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from db.database import Database
from handlers.test_auth import EMAIL, PASSWORD, csrf_headers, sign_up
from middleware.session import secret_problem

OVERLONG = "x" * 100


# --- H-1: the placeholder secret must not boot ---------------------------


@pytest.mark.parametrize(
    "secret",
    [
        "change-me-to-32-random-bytes-before-use",  # the literal env.example value
        "CHANGE-ME-TO-32-RANDOM-BYTES-BEFORE-USE",
        "please-replace-me-with-something-random!!",
        "x" * 40,  # long, but no variety
        "short",
        "",
    ],
)
def test_unusable_secrets_are_refused(secret: str) -> None:
    """This template is public, so its placeholder is a known key. A length
    check alone let it through and the app signed real sessions with it."""
    assert secret_problem(secret) is not None


@pytest.mark.parametrize(
    "secret",
    [
        "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",  # 32 hex chars
        "9f8e7d6c5b4a39281706f5e4d3c2b1a09f8e7d6c5b4a39281706f5e4d3c2b1a0",
    ],
)
def test_real_secrets_are_accepted(secret: str) -> None:
    assert secret_problem(secret) is None


# --- H-2: over-long passwords must not 500 or skip the limiter -----------


@pytest.mark.parametrize(
    ("path", "needs_csrf"),
    [("/api/v1/auth/login", True), ("/api/v1/auth/login_token", False)],
)
def test_overlong_password_is_rejected_not_a_crash(
    client: TestClient, path: str, needs_csrf: bool
) -> None:
    """bcrypt raises above 72 bytes. Uncaught, that was a 500 on an
    unauthenticated endpoint whose attempts were never counted."""
    sign_up(client)
    headers = csrf_headers(client) if needs_csrf else {}
    res = client.post(path, json={"email": EMAIL, "password": OVERLONG}, headers=headers)
    assert res.status_code == 422
    assert res.json()["fields"]["password"] == "must be at most 72 bytes"


def test_overlong_password_on_an_unknown_account_also_422(client: TestClient) -> None:
    res = client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.com", "password": OVERLONG},
        headers=csrf_headers(client),
    )
    assert res.status_code == 422


def test_the_model_layer_never_raises_on_a_long_password() -> None:
    """Defence in depth: the boundary rejects it, and the model refuses to
    crash if some future caller does not."""
    from db.testutil import open_test
    from models.user import UserModel

    with open_test() as db:
        users = UserModel(db)
        user = users.find_by_id(users.create("A", "a@b.co", PASSWORD))
        assert users.check_password(user, OVERLONG) is False
        UserModel.burn_password_time(OVERLONG)  # must not raise


# --- M-1: logout_all must reach native sessions --------------------------


def test_logout_all_revokes_bearer_tokens(client: TestClient) -> None:
    """The epoch bump only retires cookies. A user who signs out everywhere
    after a suspected compromise otherwise keeps every native session alive for
    the token's full 30-day TTL."""
    sign_up(client)
    token = client.post(
        "/api/v1/auth/login_token", json={"email": EMAIL, "password": PASSWORD}
    ).json()["data"]["token"]
    auth = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/v1/auth/me_token", headers=auth).status_code == 200

    client.post("/api/v1/auth/logout_all", headers=csrf_headers(client))
    assert client.get("/api/v1/auth/me_token", headers=auth).status_code == 401


def test_logout_all_still_retires_cookies(client: TestClient) -> None:
    sign_up(client)
    cookies = dict(client.cookies)
    client.post("/api/v1/auth/logout_all", headers=csrf_headers(client))
    client.cookies.clear()
    for name, value in cookies.items():
        client.cookies.set(name, value)
    assert client.get("/api/v1/auth/me").status_code == 401


# --- M-3: a forwarding header must be a real address ---------------------


def test_a_malformed_forwarding_header_is_not_believed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Otherwise a caller behind a trusted non-Cloudflare proxy mints a fresh
    rate-limit bucket per request by sending junk."""
    # FastAPI resolves annotations through module globals, and this file uses
    # `from __future__ import annotations` — so Request must be imported at
    # module scope or the parameter is read as a query string field.
    from handlers.clientip import client_ip, trusted_networks

    app = FastAPI()

    @app.get("/ip")
    def ip(request: Request) -> dict[str, str]:
        return {"ip": client_ip(request)}

    monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.0/8")
    trusted_networks.cache_clear()
    try:
        c = TestClient(app, client=("10.0.0.5", 1234))
        probe = c.get("/ip", headers={"CF-Connecting-IP": "not-an-ip"})
        assert probe.status_code == 200, probe.text
        assert probe.json()["ip"] == "10.0.0.5"
        assert c.get("/ip", headers={"CF-Connecting-IP": "1.2.3.4"}).json()["ip"] == "1.2.3.4"
        # A junk entry in the XFF chain is skipped rather than believed.
        assert c.get("/ip", headers={"X-Forwarded-For": "1.2.3.4, junk"}).json()["ip"] == "1.2.3.4"
    finally:
        trusted_networks.cache_clear()


# --- M-5 / L-4 / L-5: response headers and body limits -------------------


def test_api_responses_are_not_cacheable(anon: TestClient) -> None:
    assert anon.get("/api/v1/_version").headers["Cache-Control"] == "no-store"


def test_hsts_only_in_production(anon: TestClient) -> None:
    """A browser ignores HSTS over plaintext, and sending it locally would pin
    http://localhost to HTTPS in the developer's own browser."""
    assert "Strict-Transport-Security" not in anon.get("/api/v1/_version").headers


def test_an_oversized_body_is_refused(anon: TestClient) -> None:
    from middleware.bodylimit import MAX_BODY_BYTES

    res = anon.post(
        "/api/v1/auth/login",
        content=b"x" * (MAX_BODY_BYTES + 1),
        headers={"Content-Type": "application/json"},
    )
    assert res.status_code == 413


def test_a_chunked_body_cannot_slip_past_the_cap(anon: TestClient) -> None:
    """A chunked request declares no Content-Length, so a header-only check
    passed it and the whole body was buffered anyway — 3 MB went straight
    through. The cap is enforced on the bytes now, not on the claim."""
    from middleware.bodylimit import MAX_BODY_BYTES

    def stream() -> Iterator[bytes]:
        sent = 0
        while sent < MAX_BODY_BYTES * 3:
            yield b"x" * 65536
            sent += 65536

    res = anon.post(
        "/api/v1/auth/login",
        content=stream(),
        headers={"Content-Type": "application/json"},
    )
    assert res.status_code == 413
    assert res.json()["error"] == "request body too large"


def test_a_body_under_the_cap_still_streams_fine(anon: TestClient) -> None:
    """The limit must not break ordinary chunked requests."""

    def stream() -> Iterator[bytes]:
        yield b'{"email": "a@b.co", '
        yield b'"password": "hunter22222"}'

    res = anon.post(
        "/api/v1/auth/login", content=stream(), headers={"Content-Type": "application/json"}
    )
    assert res.status_code in (401, 422)  # reached the handler, not the limiter


def test_the_rejection_still_carries_security_headers(anon: TestClient) -> None:
    """The old early-return answered before the header middleware ran."""
    from middleware.bodylimit import MAX_BODY_BYTES

    res = anon.post(
        "/api/v1/auth/login",
        content=b"x" * (MAX_BODY_BYTES + 1),
        headers={"Content-Type": "application/json"},
    )
    assert res.status_code == 413
    assert res.headers["X-Content-Type-Options"] == "nosniff"
    assert "Content-Security-Policy" in res.headers


# --- L-6: the token table is bounded -------------------------------------


def test_tokens_per_user_are_capped(app: FastAPI, client: TestClient) -> None:
    """Valid credentials otherwise mint unlimited independently-usable tokens."""
    from models.mobile_token import MAX_TOKENS_PER_USER

    sign_up(client)
    for _ in range(MAX_TOKENS_PER_USER + 5):
        client.post("/api/v1/auth/login_token", json={"email": EMAIL, "password": PASSWORD})

    database = cast("Database", app.state.database)
    live = database.scalar("SELECT COUNT(*) FROM mobile_tokens")
    assert live == MAX_TOKENS_PER_USER


# --- L-2: sign-out-everywhere is one unit --------------------------------


def test_logout_all_is_atomic(client: TestClient) -> None:
    """Both halves land or neither. Run as separate autocommit statements, a
    failure between them left bearer tokens live after the user was told they
    had signed out everywhere."""
    from db.testutil import open_test
    from models.mobile_token import MobileTokenModel, generate_token, hash_token
    from models.user import UserModel

    with open_test() as db:
        users = UserModel(db)
        tokens = MobileTokenModel(db)
        user_id = users.create("A", "a@b.co", PASSWORD)
        raw = generate_token()
        tokens.issue(hash_token(raw), user_id, 4_000_000_000)

        users.revoke_all_sessions(user_id)
        assert users.session_epoch(user_id) == 1
        assert db.scalar("SELECT COUNT(*) FROM mobile_tokens") == 0


def test_a_failed_transaction_rolls_both_halves_back() -> None:
    """The epoch must not advance if the token sweep cannot run."""
    import sqlite3

    from db.testutil import open_test
    from models.user import UserModel

    with open_test() as db:
        users = UserModel(db)
        user_id = users.create("A", "a@b.co", PASSWORD)
        before = users.session_epoch(user_id)
        with pytest.raises(sqlite3.OperationalError), db.transaction() as cur:
            _ = cur.execute(
                "UPDATE users SET session_epoch = session_epoch + 1 WHERE id = ?", (user_id,)
            )
            _ = cur.execute("DELETE FROM no_such_table WHERE user_id = ?", (user_id,))
        assert users.session_epoch(user_id) == before


# --- L-4: trusted-proxy drift is visible ---------------------------------


def test_startup_logs_the_effective_trusted_ranges(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    """This setting degrades silently: the symptom is a shared rate-limit
    bucket, which looks like nothing until it locks everyone out."""
    import logging

    from handlers.clientip import trusted_networks

    monkeypatch.setenv("TRUSTED_PROXIES", "172.18.0.0/16")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    trusted_networks.cache_clear()
    try:
        import main

        with caplog.at_level(logging.INFO, logger="app"), TestClient(main.create_app()):
            pass
        assert "172.18.0.0/16" in caplog.text
    finally:
        trusted_networks.cache_clear()
