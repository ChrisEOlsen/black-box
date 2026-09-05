"""SQLite access: one writer, many readers, over a single file.

Port of gova-monolith's `db/db.go`. `database/sql` gave Go connection pooling
for free; here it is hand-rolled, because FastAPI runs sync handlers in a
threadpool and `sqlite3` connections are not shareable across threads.
See docs/DECISIONS.md § 3.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# Applied to every connection. Python's sqlite3 URI support does not carry
# these the way the Go driver's DSN query string did, so they are executed.
_PRAGMAS = (
    "PRAGMA journal_mode = WAL",
    "PRAGMA busy_timeout = 5000",
    "PRAGMA foreign_keys = ON",
    "PRAGMA synchronous = NORMAL",
)


def _connect(path: str) -> sqlite3.Connection:
    # isolation_level=None is autocommit, matching database/sql's default and
    # keeping `with db.write() as cur` the only transaction boundary.
    #
    # check_same_thread=False is safe here only because of how connections are
    # handed out below: the writer is used under a lock, and readers are
    # thread-local. Nothing is ever touched by two threads at once.
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    for pragma in _PRAGMAS:
        conn.execute(pragma)
    return conn


class Database:
    """Two pools against one SQLite file.

    A single write connection, since SQLite serializes writes anyway and a
    second one only produces SQLITE_BUSY; and one read connection per worker
    thread, created on first use.
    """

    def __init__(self, path: str = "") -> None:
        self.path = path or os.getenv("DB_PATH") or "/data/app.db"
        self._write_conn = _connect(self.path)
        self._write_lock = threading.Lock()
        self._local = threading.local()
        self._readers: list[sqlite3.Connection] = []
        self._readers_lock = threading.Lock()
        self._apply_schema()

    def _apply_schema(self) -> None:
        with self._write_lock:
            self._write_conn.executescript(SCHEMA_PATH.read_text())

    @property
    def _read_conn(self) -> sqlite3.Connection:
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is None:
            conn = _connect(self.path)
            self._local.conn = conn
            # Tracked so close() can reach connections other threads opened.
            with self._readers_lock:
                self._readers.append(conn)
        return conn

    # --- reads ------------------------------------------------------------

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        """Run a SELECT and return every row."""
        cur = self._read_conn.execute(sql, params)
        try:
            return cur.fetchall()
        finally:
            cur.close()

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        """Run a SELECT and return the first row, or None."""
        cur = self._read_conn.execute(sql, params)
        try:
            # Annotated rather than returned directly: fetchone() is typed Any,
            # and mypy --strict refuses to launder that through a return.
            row: sqlite3.Row | None = cur.fetchone()
            return row
        finally:
            cur.close()

    def scalar(self, sql: str, params: Sequence[Any] = ()) -> Any:
        """Run a SELECT and return the first column of the first row."""
        row = self.query_one(sql, params)
        return None if row is None else row[0]

    # --- writes -----------------------------------------------------------

    @contextmanager
    def write(self) -> Iterator[sqlite3.Cursor]:
        """Hold the write connection for a cursor's lifetime.

        Used where a caller needs `lastrowid` or `rowcount` from the same
        cursor that ran the statement.
        """
        with self._write_lock:
            cur = self._write_conn.cursor()
            try:
                yield cur
            finally:
                cur.close()

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Run one write and return its rowcount."""
        with self.write() as cur:
            cur.execute(sql, params)
            return cur.rowcount

    def insert(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Run one INSERT and return its new row id."""
        with self.write() as cur:
            cur.execute(sql, params)
            return int(cur.lastrowid or 0)

    def executescript(self, sql: str) -> None:
        """Run arbitrary DDL. `bb sql` is the only ordinary caller."""
        with self._write_lock:
            self._write_conn.executescript(sql)

    def close(self) -> None:
        with self._readers_lock:
            for conn in self._readers:
                conn.close()
            self._readers.clear()
        with self._write_lock:
            self._write_conn.close()
