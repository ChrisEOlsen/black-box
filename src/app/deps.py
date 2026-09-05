"""Application-wide dependency providers.

FastAPI's DI is what replaces gova-monolith's hand-threaded handler factories:
a generated handler declares `db: DatabaseDep` in its signature and the
framework supplies it. This is also why the manifest's `deps` field is
vestigial here — see docs/DECISIONS.md § 3.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from cache.cache import Cache
from db.database import Database


def get_database(request: Request) -> Database:
    """The app's Database, put on the state at startup by main.py."""
    database: Database = request.app.state.database
    return database


def get_cache(request: Request) -> Cache:
    """The app's in-process cache. One per worker, which is why the app runs a
    single uvicorn worker."""
    cache: Cache = request.app.state.cache
    return cache


DatabaseDep = Annotated[Database, Depends(get_database)]
CacheDep = Annotated[Cache, Depends(get_cache)]
