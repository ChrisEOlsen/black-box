"""A small in-process TTL cache.

In-process, so it lives and dies with the app container and is not shared
between workers — which is one of the reasons the app runs a single uvicorn
worker. See docs/DECISIONS.md § 8.

Values are JSON bytes rather than live objects: a cached list handed straight
back to two callers is one mutation away from a bug that only shows up under
load, and the round trip through Pydantic is cheap next to a query.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

# How often expired entries are swept. Expiry itself is checked on read, so
# this only reclaims memory for keys nobody asks for again.
JANITOR_INTERVAL_SECONDS = 300.0


@dataclass(frozen=True, slots=True)
class _Entry:
    value: bytes
    expires_at: float


class Cache:
    def __init__(self, *, janitor: bool = True) -> None:
        self._items: dict[str, _Entry] = {}
        self._lock = threading.Lock()
        if janitor:
            thread = threading.Thread(target=self._janitor, daemon=True, name="cache-janitor")
            thread.start()

    def get(self, key: str) -> bytes | None:
        with self._lock:
            entry = self._items.get(key)
        if entry is None or time.monotonic() > entry.expires_at:
            return None
        return entry.value

    def set(self, key: str, value: bytes, ttl_seconds: float) -> None:
        with self._lock:
            self._items[key] = _Entry(value, time.monotonic() + ttl_seconds)

    def bust(self, prefix: str) -> None:
        """Drop every key under a prefix. Models call this on any write."""
        with self._lock:
            for key in [k for k in self._items if k.startswith(prefix)]:
                del self._items[key]

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    def _janitor(self) -> None:  # pragma: no cover - background thread
        while True:
            time.sleep(JANITOR_INTERVAL_SECONDS)
            now = time.monotonic()
            with self._lock:
                for key in [k for k, e in self._items.items() if now > e.expires_at]:
                    del self._items[key]
