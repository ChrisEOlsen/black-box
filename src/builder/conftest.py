"""Builder test fixtures.

Every test gets its own workspace and its own lock file: /src is not writable
outside the builder container, and a test must never contend with a live
builder for the real lock.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from bb.tools import Workspace

REPO = Path(__file__).resolve().parents[2]
APP = REPO / "src" / "app"


@pytest.fixture
def ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Workspace]:
    """A copy of the real app tree, so scaffolds land somewhere realistic."""
    app_dir = tmp_path / "app"
    shutil.copytree(
        APP,
        app_dir,
        ignore=shutil.ignore_patterns(
            "__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache", "vendor"
        ),
    )
    monkeypatch.setenv("BB_LOCK_PATH", str(tmp_path / ".bb-lock"))
    yield Workspace(app_dir=app_dir, db_path=tmp_path / "app.db")


@pytest.fixture
def empty_ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Workspace]:
    """A bare workspace with no manifest — the state of an app that has
    scaffolded nothing yet."""
    app_dir = tmp_path / "app"
    for sub in ("handlers", "models", "static/pages", "static/js"):
        (app_dir / sub).mkdir(parents=True)
    monkeypatch.setenv("BB_LOCK_PATH", str(tmp_path / ".bb-lock"))
    yield Workspace(app_dir=app_dir, db_path=tmp_path / "app.db")


PROJECTS_TABLE = """
CREATE TABLE projects (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    status     TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""
