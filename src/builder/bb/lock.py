"""The workspace lock and atomic writes.

Every workspace mutation — api.json, the *_gen.py files, model/handler/page
files — is serialized by an flock on .bb-lock: LOCK_EX for mutators, LOCK_SH
for readers. The CLI is one process per invocation, so a single cross-process
lock covers every caller, including parallel agent sessions sharing the
bind-mounted /src.

The lock is held across the full read -> upsert -> write -> regenerate
transaction. Without it, two scaffolds read the same base manifest and the
second write silently erases the first's registration — a lost update no
same-key conflict check can see, because each writer's snapshot was already
stale. See docs/DECISIONS.md § 11.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

# Sits in /src, the bind mount every harness session shares, next to the code
# it protects.
LOCK_FILE_PATH = "/src/.bb-lock"


def lock_path() -> Path:
    """BB_LOCK_PATH overrides the target so tests can point at a temp dir.

    /src is not writable outside the builder container, and a test must never
    contend with a live builder for the real lock file.
    """
    return Path(os.getenv("BB_LOCK_PATH") or LOCK_FILE_PATH)


@contextmanager
def _flock(mode: int) -> Generator[None]:
    path = lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(handle, mode)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        os.close(handle)


@contextmanager
def workspace_lock() -> Generator[None]:
    """Exclusive. MUST NOT BE NESTED — a nested call would block on itself."""
    with _flock(fcntl.LOCK_EX):
        yield


@contextmanager
def workspace_read() -> Generator[None]:
    """Shared: concurrent readers coexist, and a mutator still excludes them."""
    with _flock(fcntl.LOCK_SH):
        yield


def atomic_write(path: Path, content: str) -> None:
    """Replace `path` via temp file + rename.

    A concurrent reader sees either the old complete file or the new complete
    one, never a truncation. The temp file is created in the destination
    directory so the rename stays within one filesystem.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(handle, "w") as out:
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
        tmp.chmod(0o644)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, payload: Any) -> None:
    atomic_write(path, json.dumps(payload, indent=2) + "\n")
