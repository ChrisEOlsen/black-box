"""The application entry point.

Routes are NEVER hand-wired here. Every API route comes from api.json via
`routes_gen.py`, and every page from the same manifest via `pages_gen.py`.
If a route is wrong, the manifest is wrong.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from cache.cache import Cache
from db.database import Database
from handlers.envelope import Envelope, register_exception_handlers
from handlers.pages_gen import register_pages
from handlers.routes_gen import register_generated
from handlers.version import VersionInfo, version
from middleware.csrf import CSRFMiddleware
from middleware.security import SecurityHeadersMiddleware
from middleware.session import secret_problem

log = logging.getLogger("app")


def configure_logging() -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_path := os.getenv("LOG_PATH"):
        try:
            handlers.append(logging.FileHandler(log_path))
        except OSError:
            # An unwritable log path must not stop the app from serving.
            pass
    logging.basicConfig(level=logging.INFO, handlers=handlers, format="%(asctime)s %(message)s")


def check_secret() -> None:
    """Refuse to boot on an unusable SESSION_SECRET.

    This is a CODE gate, not a procedural one. /build and /launch also check,
    but an agent-driven build can skip a command; it cannot skip this.
    """
    problem = secret_problem(os.getenv("SESSION_SECRET", ""))
    if problem is not None:
        raise SystemExit(f"SESSION_SECRET {problem}.\nGenerate one with: openssl rand -hex 32")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    app.state.database = Database(os.getenv("DB_PATH", ""))
    app.state.cache = Cache()
    log.info("black-box app ready")
    try:
        yield
    finally:
        app.state.database.close()


def is_production() -> bool:
    return os.getenv("APP_ENV") == "production"


def create_app() -> FastAPI:
    # Swagger is mounted only outside production, and serves vendored assets:
    # the CSP forbids a CDN, and a deployed app has no business publishing its
    # full API surface. See docs/DECISIONS.md § 10.
    app = FastAPI(
        title=os.getenv("APP_NAME", "black-box"),
        docs_url=None,
        redoc_url=None,
        openapi_url=None if is_production() else "/openapi.json",
        lifespan=lifespan,
    )

    register_exception_handlers(app)

    # Outermost first: security headers wrap everything, CSRF runs inside them
    # so a rejected request still carries the headers.
    app.add_middleware(CSRFMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)

    app.mount("/static", StaticFiles(directory="static"), name="static")

    # Home is the framework's own shell and is not in the manifest. Every other
    # page comes from api.json via pages_gen.py — never hand-wire one here.
    @app.get("/", include_in_schema=False)
    def home() -> FileResponse:
        return FileResponse(Path("static/pages/home.html"))

    register_pages(app)

    app.add_api_route(
        "/api/v1/_version", version, methods=["GET"], response_model=Envelope[VersionInfo]
    )

    # API routes from api.json via routes_gen.py — never hand-wire one here.
    register_generated(app)

    if not is_production():
        register_local_docs(app)

    return app


def register_local_docs(app: FastAPI) -> None:
    """Swagger UI from vendored assets, outside production only."""
    from fastapi.openapi.docs import get_swagger_ui_html
    from fastapi.responses import HTMLResponse

    @app.get("/docs", include_in_schema=False)
    def docs() -> HTMLResponse:
        return get_swagger_ui_html(
            openapi_url="/openapi.json",
            title=f"{app.title} — API",
            swagger_js_url="/static/vendor/swagger/swagger-ui-bundle.js",
            swagger_css_url="/static/vendor/swagger/swagger-ui.css",
        )


configure_logging()
check_secret()
app = create_app()
