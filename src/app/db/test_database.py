from __future__ import annotations

import threading

from db.testutil import open_test

SCHEMA = """
CREATE TABLE widgets (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""


def test_schema_is_applied_on_open() -> None:
    with open_test() as db:
        tables = {r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"users", "mobile_tokens", "rate_limits"} <= tables


def test_pragmas_are_set() -> None:
    with open_test() as db:
        assert str(db.scalar("PRAGMA journal_mode")).lower() == "wal"
        assert db.scalar("PRAGMA foreign_keys") == 1


def test_insert_returns_row_id_and_query_reads_back() -> None:
    with open_test(SCHEMA) as db:
        new_id = db.insert("INSERT INTO widgets (name) VALUES (?)", ("hello",))
        assert new_id == 1
        row = db.query_one("SELECT name FROM widgets WHERE id = ?", (new_id,))
        assert row is not None
        assert row["name"] == "hello"


def test_execute_reports_rowcount() -> None:
    with open_test(SCHEMA) as db:
        db.insert("INSERT INTO widgets (name) VALUES (?)", ("a",))
        assert db.execute("UPDATE widgets SET name = ? WHERE id = ?", ("b", 1)) == 1
        assert db.execute("DELETE FROM widgets WHERE id = ?", (99,)) == 0


def test_reads_work_from_several_threads() -> None:
    """Each thread gets its own read connection; sqlite3 forbids sharing one."""
    with open_test(SCHEMA) as db:
        db.insert("INSERT INTO widgets (name) VALUES (?)", ("shared",))
        seen: list[str] = []
        lock = threading.Lock()

        def read() -> None:
            row = db.query_one("SELECT name FROM widgets WHERE id = 1")
            assert row is not None
            with lock:
                seen.append(row["name"])

        threads = [threading.Thread(target=read) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert seen == ["shared"] * 8


def test_concurrent_writes_are_serialized() -> None:
    """The write lock is what keeps 20 threads from racing on one connection."""
    with open_test(SCHEMA) as db:

        def write(n: int) -> None:
            db.insert("INSERT INTO widgets (name) VALUES (?)", (f"w{n}",))

        threads = [threading.Thread(target=write, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert db.scalar("SELECT COUNT(*) FROM widgets") == 20
