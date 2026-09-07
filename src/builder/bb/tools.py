"""The six commands, and the workspace they write into.

Every mutating command holds the workspace lock across its whole
read -> render -> register -> regenerate transaction, so two harness sessions
sharing one checkout cannot lose each other's registrations.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from bb.codegen import regenerate
from bb.fields import Field, FieldError, is_safe_ident, parse_fields, to_pascal, to_plural
from bb.lock import workspace_lock, workspace_read
from bb.manifest import (
    BodySchema,
    Endpoint,
    Manifest,
    Model,
    ModelField,
    Page,
    fields_to_model,
    list_page,
    read_manifest,
    resource_endpoints,
    write_manifest,
)
from bb.render import new_data, render_to_file
from bb.schema import SchemaError, apply_schema, check_reserved_name


class ToolError(Exception):
    """A command the tools refuse, with a message for the caller."""


@dataclass(frozen=True, slots=True)
class Workspace:
    """Where the builder writes. A dataclass so tests can point at a temp tree."""

    app_dir: Path
    db_path: Path

    @property
    def manifest_path(self) -> Path:
        return self.app_dir / "api.json"

    @property
    def handlers_dir(self) -> Path:
        return self.app_dir / "handlers"

    @property
    def models_dir(self) -> Path:
        return self.app_dir / "models"

    @property
    def pages_dir(self) -> Path:
        return self.app_dir / "static" / "pages"

    @property
    def js_dir(self) -> Path:
        return self.app_dir / "static" / "js"


def default_workspace() -> Workspace:
    """The live app, unless BB_APP_DIR / BB_DB_PATH redirect it."""
    return Workspace(
        app_dir=Path(os.getenv("BB_APP_DIR") or "/src/app"),
        db_path=Path(os.getenv("BB_DB_PATH") or "/data/app.db"),
    )


# --- inspect --------------------------------------------------------------


def inspect(ws: Workspace) -> str:
    """The manifest, the files on disk, and anything registered but missing."""
    with workspace_read():
        manifest = read_manifest(ws.manifest_path)

        def scan(directory: Path, suffix: str) -> list[str]:
            if not directory.is_dir():
                return []
            return sorted(p.name for p in directory.glob(f"*{suffix}") if p.name != ".gitkeep")

        on_disk = {
            "models": scan(ws.models_dir, ".py"),
            "handlers": scan(ws.handlers_dir, ".py"),
            "pages": scan(ws.pages_dir, ".html"),
            "js": scan(ws.js_dir, ".js"),
        }

        missing_pages = [
            p.path for p in manifest.pages if not (ws.pages_dir / f"{p.file}.html").exists()
        ]
        missing_models = [
            m.name for m in manifest.models if not (ws.models_dir / f"{m.name}.py").exists()
        ]

        report = {
            "api_version": manifest.api_version,
            "hash": manifest.hash(),
            "models": [m.name for m in manifest.models],
            "endpoints": [f"{e.method} {e.path}" for e in manifest.endpoints],
            "pages": [p.path for p in manifest.pages],
            "on_disk": on_disk,
            "divergence": {
                "registered_pages_without_a_shell": missing_pages,
                "registered_models_without_a_file": missing_models,
            },
        }
        return json.dumps(report, indent=2)


# --- sql ------------------------------------------------------------------


def execute_sql(ws: Workspace, query: str) -> str:
    if not query.strip():
        raise ToolError("-query is required")
    # DDL joins the workspace lock: several simultaneous CREATE TABLEs can
    # exhaust the busy timeout and hand a caller a SQLITE_BUSY it did nothing
    # to earn. Serializing turns contention into waiting.
    with workspace_lock():
        ws.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(ws.db_path)
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.executescript(query)
            conn.commit()
        except sqlite3.Error as exc:
            raise ToolError(str(exc)) from exc
        finally:
            conn.close()
    return "SQL executed successfully"


# --- shared model preparation --------------------------------------------


def prepare_model(ws: Workspace, name: str, raw_fields: list[str]) -> list[Field]:
    """The checks every model-producing command shares."""
    if not is_safe_ident(name):
        raise ToolError("-name must be alphanumeric and underscore only")
    try:
        check_reserved_name(name)
    except SchemaError as exc:
        raise ToolError(str(exc)) from exc

    fields = parse_fields(raw_fields)
    if not fields:
        raise ToolError("-fields is required (at least one name:type pair)")
    try:
        fields = apply_schema(str(ws.db_path), to_plural(name), fields)
    except (SchemaError, FieldError) as exc:
        # Both surface as a clean CLI message. Without FieldError here, a typo
        # in a field type printed a traceback instead.
        raise ToolError(str(exc)) from exc
    validate_refs(ws, fields)
    return fields


def validate_refs(ws: Workspace, fields: list[Field]) -> None:
    """A foreign key must name a model that already exists."""
    refs = [f.ref for f in fields if f.ref]
    if not refs:
        return
    known = {m.name for m in read_manifest(ws.manifest_path).models}
    for ref in refs:
        if ref not in known:
            raise ToolError(
                f"field references unknown model {ref!r} — scaffold it first "
                f"(known models: {', '.join(sorted(known)) or 'none'})"
            )


def commit(
    ws: Workspace,
    models: list[Model] | None = None,
    endpoints: list[Endpoint] | None = None,
    pages: list[Page] | None = None,
) -> None:
    """Register into api.json and regenerate. Call inside the workspace lock."""
    manifest = read_manifest(ws.manifest_path)
    for model in models or []:
        manifest.upsert_model(model)
    for endpoint in endpoints or []:
        manifest.upsert_endpoint(endpoint)
    for page in pages or []:
        manifest.upsert_page(page)
    write_manifest(ws.manifest_path, manifest, datetime.now(UTC))
    regenerate(ws.handlers_dir, manifest)


# --- model ----------------------------------------------------------------


def create_model(ws: Workspace, name: str, raw_fields: list[str]) -> str:
    with workspace_lock():
        fields = prepare_model(ws, name, raw_fields)
        data = new_data(name, fields).as_dict()
        written = [
            render_file(ws.models_dir / f"{name}.py", "model.py.j2", data),
            render_file(ws.models_dir / f"test_{name}.py", "test_model.py.j2", data),
        ]
        # A model registers no route, but it does register: a model missing
        # from the manifest while endpoints reference it is a hole nothing
        # goes looking for.
        commit(ws, models=[fields_to_model(name, to_plural(name), fields)])
    return report(written) + f"\n\nRegistered model {name} in api.json."


# --- handler --------------------------------------------------------------

ALLOWED_METHODS = ("GET", "POST", "PUT", "DELETE")


def create_handler(
    ws: Workspace,
    name: str,
    method: str,
    path: str,
    *,
    auth: bool = True,
    summary: str = "",
    request_schema: str = "",
    response_schema: str = "",
) -> str:
    if not is_safe_ident(name):
        raise ToolError("-name must be alphanumeric and underscore only")
    method = method.strip().upper()
    if method not in ALLOWED_METHODS:
        raise ToolError(f"-method must be one of {', '.join(ALLOWED_METHODS)}, got {method!r}")
    if not path.startswith("/api/v1/"):
        raise ToolError("-path must start with /api/v1/")

    request = parse_body_schema("-request-schema", request_schema)
    response = parse_body_schema("-response-schema", response_schema)

    with workspace_lock():
        data = new_data(name).as_dict()
        data["method"] = f"{method} {path}"
        written = [render_file(ws.handlers_dir / f"{name}.py", "handler.py.j2", data)]
        commit(
            ws,
            endpoints=[
                Endpoint(
                    method=method,
                    path=path,
                    handler=name,
                    module=name,
                    deps=["db", "cache"],
                    auth=auth,
                    kind="custom",
                    summary=summary,
                    request=request,
                    response=response,
                )
            ],
        )
    return (
        report(written)
        + f"\nRegistered {method} {path} in api.json + routes_gen.py."
        + "\nImplement the body, then write a test for it."
    )


def parse_body_schema(flag: str, raw: str) -> BodySchema | None:
    """Parse a -request-schema / -response-schema argument.

    Every member is type-checked rather than trusted. This is a CLI flag an
    agent composes, and whatever comes out of it is written verbatim into
    api.json — which a native client then reads. A wrong type here would be
    persisted, not merely rejected.
    """
    if not raw.strip():
        return None
    try:
        decoded: object = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ToolError(f"{flag}: not valid JSON: {exc}") from exc
    if not isinstance(decoded, dict):
        raise ToolError(f"{flag}: must be a JSON object")
    parsed = cast("dict[str, object]", decoded)

    shape = parsed.get("shape", "object")
    if not isinstance(shape, str) or shape not in ("object", "list", "empty"):
        raise ToolError(f'{flag}: shape must be "object", "list" or "empty"')

    model = parsed.get("model", "")
    if not isinstance(model, str):
        raise ToolError(f"{flag}: model must be a string naming a scaffolded model")

    raw_fields = parsed.get("fields", [])
    if not isinstance(raw_fields, list):
        raise ToolError(f"{flag}: fields must be a list of objects")

    fields: list[ModelField] = []
    for entry in cast("list[object]", raw_fields):
        if not isinstance(entry, dict):
            raise ToolError(f"{flag}: each entry in fields must be an object")
        field = cast("dict[str, object]", entry)
        for key in ("name", "type"):
            if not isinstance(field.get(key), str):
                raise ToolError(f"{flag}: every field needs a string {key!r}")
        fields.append(ModelField.from_json(field))

    if model and fields:
        raise ToolError(f"{flag}: set either model or fields, never both")
    return BodySchema(shape=shape, model=model, fields=fields)


# --- page -----------------------------------------------------------------


def create_page(ws: Workspace, file: str, title: str, path: str, *, auth: bool = True) -> str:
    if not is_safe_ident(file):
        raise ToolError("-file must be alphanumeric and underscore only")
    if not title.strip():
        raise ToolError("-title is required")
    validate_page_path(path)

    with workspace_lock():
        data = new_data(file).as_dict()
        data["title"] = title
        data["auth_required"] = auth
        written = [
            render_file(ws.pages_dir / f"{file}.html", "page.html.j2", data),
            render_file(ws.js_dir / f"{file}.js", "page.js.j2", data),
        ]
        commit(ws, pages=[Page(path=path, file=file, title=title, auth=auth)])
    return (
        report(written)
        + f"\nRegistered page {path} in api.json + pages_gen.py."
        + "\nThe generated page_file helper serves the shell — there is no handler to write."
        + "\nAdd the JSON endpoints its module calls with `bb handler`."
    )


def validate_page_path(path: str) -> None:
    """The page namespace: a human-facing URL, never an API one.

    The exact inverse of create_handler's check, which is what makes the two
    manifest tables provably disjoint.
    """
    if not path.startswith("/"):
        raise ToolError("-path must start with /")
    if path == "/api" or path.startswith("/api/"):
        raise ToolError(
            "-path must not be under /api/ — that namespace belongs to `bb handler`; "
            "give the page a human-facing URL like /projects"
        )
    if path.startswith("/static/"):
        raise ToolError("-path must not be under /static/ — that prefix is the static file server")


# --- resource -------------------------------------------------------------


def scaffold_resource(ws: Workspace, name: str, raw_fields: list[str], *, auth: bool = True) -> str:
    with workspace_lock():
        fields = prepare_model(ws, name, raw_fields)
        plural = to_plural(name)
        title = to_pascal(plural)

        data = new_data(name, fields).as_dict()
        data["crud"] = True
        data["title"] = title
        data["auth_required"] = auth

        written = [
            render_file(ws.models_dir / f"{name}.py", "model.py.j2", data),
            render_file(ws.models_dir / f"test_{name}.py", "test_model.py.j2", data),
            render_file(ws.handlers_dir / f"{name}_resource.py", "resource_handlers.py.j2", data),
            render_file(
                ws.handlers_dir / f"test_{name}_resource.py",
                "test_resource_handlers.py.j2",
                data,
            ),
            render_file(ws.pages_dir / f"{plural}.html", "list_page.html.j2", data),
            render_file(ws.js_dir / f"{plural}.js", "list_page.js.j2", data),
        ]

        model = fields_to_model(name, plural, fields)
        commit(
            ws,
            models=[model],
            endpoints=resource_endpoints(model, auth=auth),
            pages=[list_page(name, title, auth=auth)],
        )

    return (
        report(written)
        + f"\n\nRegistered CRUD for /api/v1/{plural} and the page /{plural}"
        + " in api.json + routes_gen.py + pages_gen.py."
        + "\nThe page includes a create form and delete buttons."
        + (
            "\nEndpoints and page require a signed-in user."
            if auth
            else "\nWARNING: scaffolded -public. Anyone can create, update and delete these."
        )
    )


# --- helpers --------------------------------------------------------------


def render_file(path: Path, template: str, data: dict[str, object]) -> Path:
    render_to_file(template, path, data)
    return path


def report(written: list[Path]) -> str:
    return "\n".join(f"Created: {p}" for p in written)


def empty_manifest() -> Manifest:
    return Manifest()
