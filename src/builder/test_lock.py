"""The workspace lock is what keeps two harness sessions from erasing each
other's registrations. Tested with real processes, because the guarantee is a
cross-process flock — threads in one interpreter would prove nothing."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from bb.lock import atomic_write, workspace_lock, workspace_read
from bb.manifest import read_manifest
from bb.tools import Workspace

RESOURCES = ["alpha", "beta", "gamma", "delta"]

WORKER = """
import sys
sys.path.insert(0, {builder!r})
from bb.cli import run
from bb.tools import Workspace
from pathlib import Path
ws = Workspace(app_dir=Path({app!r}), db_path=Path({db!r}))
run(["resource", "-name", sys.argv[1], "-fields", "name:string"], ws)
"""


def test_four_concurrent_scaffolds_all_register(ws: Workspace, tmp_path: Path) -> None:
    """Without a lock held across the whole read-write-regenerate cycle, two
    scaffolds read the same base manifest and the second erases the first."""
    for name in RESOURCES:
        run_sql = f"""CREATE TABLE {name}s (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP);"""
        from bb.cli import run

        run(["sql", "-query", run_sql], ws)

    builder_dir = str(Path(__file__).resolve().parent)
    script = tmp_path / "worker.py"
    script.write_text(WORKER.format(builder=builder_dir, app=str(ws.app_dir), db=str(ws.db_path)))

    env = {**os.environ, "BB_LOCK_PATH": os.environ["BB_LOCK_PATH"]}
    processes = [
        subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            [sys.executable, str(script), name],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for name in RESOURCES
    ]
    for process in processes:
        _, err = process.communicate(timeout=120)
        assert process.returncode == 0, err.decode()

    manifest = read_manifest(ws.manifest_path)
    registered = {m.name for m in manifest.models}
    assert set(RESOURCES) <= registered, f"lost a registration: {registered}"

    # Every resource's five routes survived too, not just its model row.
    paths = {e.path for e in manifest.endpoints}
    for name in RESOURCES:
        assert f"/api/v1/{name}s" in paths
        assert f"/api/v1/{name}s/{{id}}" in paths

    # And every file each scaffold should have written is really on disk —
    # proof the subprocesses did the work rather than exiting early.
    for name in RESOURCES:
        assert (ws.models_dir / f"{name}.py").is_file()
        assert (ws.handlers_dir / f"{name}_resource.py").is_file()
        assert (ws.js_dir / f"{name}s.js").is_file()

    # The last writer's regenerated routes file names all four, which is the
    # thing a lost update would silently break.
    routes = (ws.handlers_dir / "routes_gen.py").read_text()
    for name in RESOURCES:
        assert f"{name}_resource.{name}_list" in routes


def test_the_manifest_is_still_valid_json_after_the_race(ws: Workspace) -> None:
    """Atomic writes mean a reader sees a complete file or the previous one,
    never a truncation."""
    json.loads(ws.manifest_path.read_text())


def test_atomic_write_leaves_no_temp_files(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"
    atomic_write(target, "hello")
    assert target.read_text() == "hello"
    assert list(tmp_path.iterdir()) == [target]


def test_atomic_write_replaces_rather_than_appends(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"
    atomic_write(target, "first")
    atomic_write(target, "second")
    assert target.read_text() == "second"


def test_shared_locks_do_not_exclude_each_other(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("BB_LOCK_PATH", str(tmp_path / "lock"))
    with workspace_read(), workspace_read():
        pass  # two readers coexist


def test_the_exclusive_lock_is_released(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BB_LOCK_PATH", str(tmp_path / "lock"))
    with workspace_lock():
        pass
    with workspace_lock():
        pass  # would hang if the first were not released
