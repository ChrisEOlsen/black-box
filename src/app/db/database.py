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
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Final, cast, final

SCHEMA_PATH: Final = Path(__file__).with_name("schema.sql")

# What SQLite can bind natively. We register no adapters — Python's datetime
# adapters are deprecated since 3.12 — so a `datetime` is NOT bindable here;
# models convert through `models.timestamp.timestamp_to_db` first.
#
# Spelled out rather than `Sequence[Any]` so that passing a datetime is a type
# error at the call site instead of an `InterfaceError` at runtime. `bool` binds
# because it is a subclass of `int`.
type SQLValue = str | bytes | int | float | None
type SQLParams = Sequence[SQLValue]

# Applied to every connection. Python's sqlite3 URI support does not carry
# these the way the Go driver's DSN query string did, so they are executed.
_PRAGMAS: Final = (
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
        _ = conn.execute(pragma)
    return conn


@final
class Database:
    """Two pools against one SQLite file.

    A single write connection, since SQLite serializes writes anyway and a
    second one only produces SQLITE_BUSY; and one read connection per worker
    thread, created on first use.

    `@final` is intent, not decoration: the connection handling above is only
    correct as written, and a subclass overriding it would break the threading
    guarantee silently.
    """

    path: str
    _write_conn: sqlite3.Connection
    _write_lock: threading.Lock
    _local: threading.local
    _readers: list[sqlite3.Connection]
    _readers_lock: threading.Lock

    def __init__(self, path: str = "") -> None:
        self.path = path or os.getenv("DB_PATH") or "/data/app.db"
        self._write_conn = _connect(self.path)
        self._write_lock = threading.Lock()
        self._local = threading.local()
        self._readers = []
        self._readers_lock = threading.Lock()
        self._apply_schema()

    def _apply_schema(self) -> None:
        with self._write_lock:
            _ = self._write_conn.executescript(SCHEMA_PATH.read_text())

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

    def query(self, sql: str, params: SQLParams = ()) -> list[sqlite3.Row]:
        """Run a SELECT and return every row."""
        cur = self._read_conn.execute(sql, params)
        try:
            # The cursor's row_factory makes these Rows; the stubs type the
            # fetch methods as Any, so the cast is where that is pinned down.
            return cast("list[sqlite3.Row]", cur.fetchall())
        finally:
            cur.close()

    def query_one(self, sql: str, params: SQLParams = ()) -> sqlite3.Row | None:
        """Run a SELECT and return the first row, or None."""
        cur = self._read_conn.execute(sql, params)
        try:
            return cast("sqlite3.Row | None", cur.fetchone())
        finally:
            cur.close()

    def scalar(self, sql: str, params: SQLParams = ()) -> SQLValue:
        """Run a SELECT and return the first column of the first row."""
        row = self.query_one(sql, params)
        return None if row is None else cast("SQLValue", row[0])

    # --- writes -----------------------------------------------------------

    @contextmanager
    def write(self) -> Generator[sqlite3.Cursor]:
        """Hold the write connection for a cursor's lifetime.

        Used where a caller needs `lastrowid` or `rowcount` from the same
        cursor that ran the statement.

        NOT reentrant: `execute`, `insert` and `executescript` all take this
        lock, so calling one from inside a `with db.write()` block deadlocks.
        Run the statements on the cursor you already hold instead.
        """
        with self._write_lock:
            cur = self._write_conn.cursor()
            try:
                yield cur
            finally:
                cur.close()

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Cursor]:
        """Run several statements as one unit.

        The connection is in autocommit, so `write()` commits each statement as
        it runs. Where two writes are one promise to the user, this is what
        makes a crash between them impossible.
        """
        with self._write_lock:
            cur = self._write_conn.cursor()
            try:
                _ = cur.execute("BEGIN IMMEDIATE")
                yield cur
                self._write_conn.commit()
            except BaseException:
                self._write_conn.rollback()
                raise
            finally:
                cur.close()

    def execute(self, sql: str, params: SQLParams = ()) -> int:
        """Run one write and return its rowcount."""
        with self.write() as cur:
            _ = cur.execute(sql, params)
            return cur.rowcount

    def insert(self, sql: str, params: SQLParams = ()) -> int:
        """Run one INSERT and return its new row id."""
        with self.write() as cur:
            _ = cur.execute(sql, params)
            return int(cur.lastrowid or 0)

    def executescript(self, sql: str) -> None:
        """Run arbitrary DDL. `bb sql` is the only ordinary caller."""
        with self._write_lock:
            _ = self._write_conn.executescript(sql)

    def close(self) -> None:
        with self._readers_lock:
            for conn in self._readers:
                conn.close()
            self._readers.clear()
        with self._write_lock:
            self._write_conn.close()
