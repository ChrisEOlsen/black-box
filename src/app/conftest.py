"""Shared fixtures.

Every test that touches the app gets a fresh temporary database, so nothing
here can reach /data/app.db.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

TEST_SECRET = "test-secret-that-is-long-enough-for-hmac-0123456789"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A known secret, and never production, for every test."""
    monkeypatch.setenv("SESSION_SECRET", TEST_SECRET)
    monkeypatch.setenv("APP_ENV", "local")
    monkeypatch.setenv("TRUSTED_PROXIES", "")


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    """The real application, over a temporary database."""
    import main

    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setenv("DB_PATH", str(Path(tmp) / "test.db"))
        monkeypatch.chdir(Path(__file__).parent)
        yield main.create_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def anon(app: FastAPI) -> Iterator[TestClient]:
    """A client that does not persist cookies between requests."""
    with TestClient(app) as test_client:
        test_client.cookies.clear()
        yield test_client
