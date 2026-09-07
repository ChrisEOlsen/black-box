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
from handlers.clientip import trusted_networks
from handlers.envelope import Envelope, register_exception_handlers
from handlers.pages_gen import register_pages
from handlers.routes_gen import register_generated
from handlers.version import VersionInfo, version
from middleware.bodylimit import BodySizeLimitMiddleware
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

    # Logged every boot because this setting silently degrades. If the compose
    # network is recreated on a different subnet, the app stops believing
    # CF-Connecting-IP and every caller shares one rate-limit bucket again —
    # five bad logins then lock out the whole deployment. The symptom is
    # invisible; this line is where you see the cause.
    networks = trusted_networks()
    if networks:
        log.info("trusting forwarded headers from: %s", ", ".join(str(n) for n in networks))
    elif is_production():
        log.warning(
            "TRUSTED_PROXIES is empty in production. If anything sits in front of this "
            "app, every caller shares one rate-limit bucket. See /launch step 3."
        )

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

    # Last added is outermost. Security headers therefore wrap everything —
    # including the body-limit rejection, which previously shipped bare — and
    # the body limit sits outside CSRF so an oversized request is refused
    # before anything downstream buffers it.
    app.add_middleware(CSRFMiddleware)
    app.add_middleware(BodySizeLimitMiddleware)
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
