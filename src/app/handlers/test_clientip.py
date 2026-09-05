from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from handlers.clientip import client_ip, trusted_networks


@pytest.fixture(autouse=True)
def _clear_cache() -> Iterator[None]:
    trusted_networks.cache_clear()
    yield
    trusted_networks.cache_clear()


def make_client() -> TestClient:
    app = FastAPI()

    @app.get("/ip")
    def ip(request: Request) -> dict[str, str]:
        return {"ip": client_ip(request)}

    # TestClient reports the peer as "testclient" unless told otherwise.
    return TestClient(app, client=("10.0.0.5", 1234))


def resolve(headers: dict[str, str] | None = None) -> str:
    return str(make_client().get("/ip", headers=headers or {}).json()["ip"])


def test_untrusted_peer_names_only_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRUSTED_PROXIES", "")
    assert resolve({"X-Forwarded-For": "1.2.3.4"}) == "10.0.0.5"


def test_untrusted_peer_cannot_forge_cloudflare_header(monkeypatch: pytest.MonkeyPatch) -> None:
    """Otherwise a direct caller mints a fresh rate-limit bucket per request."""
    monkeypatch.setenv("TRUSTED_PROXIES", "")
    assert resolve({"CF-Connecting-IP": "1.2.3.4"}) == "10.0.0.5"


def test_trusted_peer_cloudflare_header_is_believed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.0/8")
    assert resolve({"CF-Connecting-IP": "1.2.3.4"}) == "1.2.3.4"


def test_trusted_peer_takes_rightmost_untrusted_forwarded_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Everything left of the first trusted hop is attacker-authored."""
    monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.0/8")
    assert resolve({"X-Forwarded-For": "9.9.9.9, 1.2.3.4"}) == "1.2.3.4"


def test_trusted_hops_are_skipped_within_the_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.0/8")
    assert resolve({"X-Forwarded-For": "1.2.3.4, 10.0.0.7, 10.0.0.8"}) == "1.2.3.4"


def test_trusted_peer_with_no_headers_falls_back_to_the_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRUSTED_PROXIES", "10.0.0.0/8")
    assert resolve() == "10.0.0.5"


def test_malformed_cidr_entries_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRUSTED_PROXIES", "not-a-cidr, 10.0.0.0/8")
    assert trusted_networks() != ()
    assert resolve({"CF-Connecting-IP": "1.2.3.4"}) == "1.2.3.4"
