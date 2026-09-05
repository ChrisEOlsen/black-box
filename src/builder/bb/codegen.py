"""Regenerating routes_gen.py and pages_gen.py from the manifest.

Nobody hand-wires a route: if a route is wrong, the manifest is wrong.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bb.fields import to_pascal
from bb.manifest import Endpoint, Manifest, Page
from bb.render import render_to_file

# Handlers that ship with the template rather than being scaffolded. Their
# module and response type cannot be derived from the manifest, so they are
# named here — the one place the generator knows about committed code.
AUTH_MODULES = {
    "login": ("auth", "PublicUser"),
    "logout": ("auth", "Status"),
    "logout_all": ("auth", "Status"),
    "me": ("auth", "PublicUser"),
    "register": ("register", "PublicUser"),
    "login_token": ("auth_mobile", "TokenGrant"),
    "logout_token": ("auth_mobile", "Status"),
    "me_token": ("auth_mobile", "PublicUser"),
}

# Where each of those response types is defined. mypy --strict forbids implicit
# re-export, so a generated file must import a type from the module that
# defines it, not from a module that merely imported it.
RESPONSE_TYPE_IMPORTS = {
    "PublicUser": "from models.user import PublicUser",
    "Status": "from handlers.auth import Status",
    "TokenGrant": "from handlers.auth_mobile import TokenGrant",
}


def _endpoint_module(endpoint: Endpoint) -> str:
    if endpoint.module:
        return endpoint.module
    known = AUTH_MODULES.get(endpoint.handler)
    return known[0] if known else endpoint.handler


def _response_model(endpoint: Endpoint) -> tuple[str, str | None]:
    """The `response_model=` expression, and the import line it needs."""
    known = AUTH_MODULES.get(endpoint.handler)
    if known is not None:
        type_name = known[1]
        return f"Envelope[{type_name}]", RESPONSE_TYPE_IMPORTS[type_name]

    if endpoint.model:
        model_type = to_pascal(endpoint.model)
        import_line = f"from models.{endpoint.model} import {model_type}"
        if endpoint.response and endpoint.response.shape == "list":
            return f"Envelope[list[{model_type}]]", import_line
        if endpoint.kind == "delete":
            return "Envelope[dict[str, str]]", None
        return f"Envelope[{model_type}]", import_line

    # A custom `bb handler` endpoint declares its own shape in its module.
    return "None", None


def routes_data(manifest: Manifest) -> dict[str, Any]:
    manifest.canonicalize()
    routes: list[dict[str, object]] = []
    modules: set[str] = set()
    imports: set[str] = set()
    uses_auth = False

    for endpoint in manifest.endpoints:
        module = _endpoint_module(endpoint)
        modules.add(module)
        response_model, import_line = _response_model(endpoint)
        if import_line:
            imports.add(import_line)
        uses_auth = uses_auth or endpoint.auth
        routes.append(
            {
                "path": endpoint.path,
                "method": endpoint.method,
                "call": f"{module}.{endpoint.handler}",
                "response_model": response_model,
                "auth": endpoint.auth,
            }
        )

    return {
        "routes": routes,
        "modules": sorted(modules),
        "model_imports": sorted(imports),
        "uses_auth": uses_auth,
    }


def pages_data(manifest: Manifest) -> dict[str, Any]:
    manifest.canonicalize()
    pages: list[Page] = manifest.pages
    return {
        "pages": pages,
        "any_auth": any(p.auth for p in pages),
        "public_pages": [p for p in pages if not p.auth],
        "guarded_pages": [p for p in pages if p.auth],
    }


def regenerate(handlers_dir: Path, manifest: Manifest) -> None:
    """Rewrite every generated file from the manifest."""
    render_to_file("routes_gen.py.j2", handlers_dir / "routes_gen.py", routes_data(manifest))
    pages = pages_data(manifest)
    render_to_file("pages_gen.py.j2", handlers_dir / "pages_gen.py", pages)
    render_to_file("test_pages_gen.py.j2", handlers_dir / "test_pages_gen.py", pages)
