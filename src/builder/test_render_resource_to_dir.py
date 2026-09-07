"""The generator's compile test.

Python has no `go build`, so this is the only thing standing between a bad
template and a runtime 500: render a resource into a scratch copy of the real
app tree, then run ruff, mypy and pytest over the result.

Every other builder test proves the generator produces the STRINGS we expect.
This one proves the strings are working Python. See docs/DECISIONS.md § 1.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from bb.cli import run
from bb.tools import Workspace

REPO = Path(__file__).resolve().parents[2]
APP = REPO / "src" / "app"
VENV_BIN = REPO / ".venv" / "bin"

TABLE = """
CREATE TABLE projects (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    status     TEXT,
    budget     REAL,
    active     INTEGER NOT NULL,
    due_at     DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

FIELDS = "name:string,status:string,budget:float,active:boolean,due_at:timestamp"


@pytest.fixture(scope="module")
def scaffolded(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    """A full copy of src/app with one scaffolded resource in it."""
    scratch = tmp_path_factory.mktemp("app")
    app_dir = scratch / "app"
    shutil.copytree(
        APP,
        app_dir,
        # vendor/ is copied too: an app test asserts the Swagger assets are
        # served locally rather than from a CDN.
        ignore=shutil.ignore_patterns("__pycache__", ".mypy_cache", ".ruff_cache", ".pytest_cache"),
    )
    ws = Workspace(app_dir=app_dir, db_path=scratch / "app.db")

    os.environ["BB_LOCK_PATH"] = str(scratch / ".bb-lock")
    run(["sql", "-query", TABLE], ws)
    run(["resource", "-name", "project", "-fields", FIELDS], ws)
    yield app_dir


def tool(name: str) -> str:
    path = VENV_BIN / name
    if not path.is_file():  # pragma: no cover - the venv is created by scripts/verify
        pytest.skip(f"{name} is not installed in {VENV_BIN}")
    return str(path)


def check(app_dir: Path, *cmd: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        cmd, cwd=app_dir, capture_output=True, text=True, check=False
    )


def test_every_expected_file_was_written(scaffolded: Path) -> None:
    for relative in (
        "models/project.py",
        "models/test_project.py",
        "handlers/project_resource.py",
        "handlers/test_project_resource.py",
        "static/pages/projects.html",
        "static/js/projects.js",
    ):
        assert (scaffolded / relative).is_file(), relative


def test_generated_code_passes_ruff(scaffolded: Path) -> None:
    result = check(scaffolded, tool("ruff"), "check", ".")
    assert result.returncode == 0, result.stdout + result.stderr


def test_generated_code_is_already_formatted(scaffolded: Path) -> None:
    """The builder formats what it writes, so a fresh scaffold is clean."""
    result = check(scaffolded, tool("ruff"), "format", "--check", ".")
    assert result.returncode == 0, result.stdout + result.stderr


def test_generated_code_type_checks(scaffolded: Path) -> None:
    """mypy --strict is what stands in for the Go compiler."""
    result = check(scaffolded, tool("mypy"), ".")
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_whole_scaffolded_app_passes_its_tests(scaffolded: Path) -> None:
    """Including the tests the scaffold itself generated."""
    env = {**os.environ, "PYTHONPATH": str(scaffolded)}
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        [tool("pytest"), "-q"],
        cwd=scaffolded,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode == 0, result.stdout[-6000:] + result.stderr[-2000:]


def test_the_app_still_imports(scaffolded: Path) -> None:
    """A generated route referencing a missing symbol fails here, not in
    production."""
    env = {
        **os.environ,
        "PYTHONPATH": str(scaffolded),
        # A realistic value: the app refuses low-variety secrets outright.
        "SESSION_SECRET": "9f8e7d6c5b4a39281706f5e4d3c2b1a0",
        "DB_PATH": str(scaffolded.parent / "app.db"),
    }
    result = subprocess.run(
        [sys.executable, "-c", "import main; main.create_app()"],
        cwd=scaffolded,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
