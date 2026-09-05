"""Test database helper.

Anything touching the database opens through `open_test`, which is a temp file
that is deleted afterwards — never `/data/app.db`. The app schema is applied by
`Database` itself; `extra_schema` adds the table under test.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from db.database import Database


@contextmanager
def open_test(extra_schema: str = "") -> Iterator[Database]:
    """Yield a Database backed by a temporary file."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(str(Path(tmp) / "test.db"))
        if extra_schema:
            db.executescript(extra_schema)
        try:
            yield db
        finally:
            db.close()
