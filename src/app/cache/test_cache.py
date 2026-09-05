from __future__ import annotations

import threading

from cache.cache import Cache


def make() -> Cache:
    # No janitor: a background thread in a unit test is a flake waiting to
    # happen, and expiry is checked on read anyway.
    return Cache(janitor=False)


def test_set_then_get_round_trips() -> None:
    c = make()
    c.set("k", b"v", 60)
    assert c.get("k") == b"v"


def test_missing_key_is_none() -> None:
    assert make().get("nope") is None


def test_expired_entry_is_not_returned() -> None:
    c = make()
    c.set("k", b"v", -1)
    assert c.get("k") is None


def test_bust_drops_the_whole_prefix_and_nothing_else() -> None:
    c = make()
    c.set("projects:page:1", b"a", 60)
    c.set("projects:page:2", b"b", 60)
    c.set("clients:page:1", b"c", 60)
    c.bust("projects:")
    assert c.get("projects:page:1") is None
    assert c.get("projects:page:2") is None
    assert c.get("clients:page:1") == b"c"


def test_concurrent_writers_do_not_lose_entries() -> None:
    c = make()

    def fill(n: int) -> None:
        for i in range(50):
            c.set(f"k{n}:{i}", b"v", 60)

    threads = [threading.Thread(target=fill, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(c.get(f"k{n}:{i}") == b"v" for n in range(8) for i in range(50))
