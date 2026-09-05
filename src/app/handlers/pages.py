"""Serving page shells.

A page is inert HTML: no data, no rendering, no template engine. Every datum on
it arrives from an `/api/v1/` endpoint fetched by its ES module. Serving a
static file is not rendering — see CLAUDE.md § Critical Constraints.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from fastapi.responses import FileResponse

PAGES_DIR = Path("static/pages")


def page_file(name: str) -> Callable[[], FileResponse]:
    """Serve a page's static HTML shell.

    `name` is a literal from the generated page table, never from a request.
    `Path(name).name` is a second guard, so the result can only name a file
    directly inside static/pages.
    """
    path = PAGES_DIR / f"{Path(name).name}.html"

    def serve() -> FileResponse:
        return FileResponse(path)

    return serve
