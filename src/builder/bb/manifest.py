"""api.json — the committed source of truth for the served surface.

The builder writes it; routes and page routes are generated from it. It is also
read directly off disk by gova-ios's export_manifest.py, which is why the
schema here is frozen: see docs/API-CONTRACT.md § The manifest.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bb.fields import Field, to_plural
from bb.lock import atomic_write_json

# Stamped into api.json so an app can say which build of the generator wrote
# it. Bump whenever anything under src/builder changes.
BUILDER_VERSION = "2026-09-05.1"

API_VERSION = "1.0.0"


class ManifestError(Exception):
    """Two scaffolds claiming one route, page, or name."""


@dataclass(slots=True)
class ModelField:
    name: str
    type: str
    nullable: bool = False
    format: str = ""
    references: str = ""

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "type": self.type, "nullable": self.nullable}
        if self.format:
            out["format"] = self.format
        if self.references:
            out["references"] = self.references
        return out

    @staticmethod
    def from_json(raw: dict[str, Any]) -> ModelField:
        return ModelField(
            name=raw["name"],
            type=raw["type"],
            nullable=bool(raw.get("nullable", False)),
            format=raw.get("format", ""),
            references=raw.get("references", ""),
        )


@dataclass(slots=True)
class BodySchema:
    """An endpoint's request or response body.

    Either `model` (fields inherited from that model) or `fields` (inline) is
    set, never both for a given schema.
    """

    shape: str
    model: str = ""
    fields: list[ModelField] = dataclass_field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"shape": self.shape}
        if self.model:
            out["model"] = self.model
        if self.fields:
            out["fields"] = [f.to_json() for f in self.fields]
        return out

    @staticmethod
    def from_json(raw: dict[str, Any]) -> BodySchema:
        return BodySchema(
            shape=raw.get("shape", "object"),
            model=raw.get("model", ""),
            fields=[ModelField.from_json(f) for f in raw.get("fields", [])],
        )


@dataclass(slots=True)
class Model:
    name: str
    table: str
    fields: list[ModelField]

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "table": self.table,
            "fields": [f.to_json() for f in self.fields],
        }

    @staticmethod
    def from_json(raw: dict[str, Any]) -> Model:
        return Model(
            name=raw["name"],
            table=raw["table"],
            fields=[ModelField.from_json(f) for f in raw.get("fields", [])],
        )


@dataclass(slots=True)
class Endpoint:
    method: str
    path: str
    handler: str
    module: str = ""
    deps: list[str] = dataclass_field(default_factory=list)
    auth: bool = False
    model: str = ""
    kind: str = "custom"
    summary: str = ""
    request: BodySchema | None = None
    response: BodySchema | None = None

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "method": self.method,
            "path": self.path,
            "handler": self.handler,
            "deps": self.deps,
            "auth": self.auth,
        }
        if self.model:
            out["model"] = self.model
        out["kind"] = self.kind
        if self.summary:
            out["summary"] = self.summary
        if self.request:
            out["request"] = self.request.to_json()
        if self.response:
            out["response"] = self.response.to_json()
        # Internal: which module holds the handler. Not part of the contract,
        # and no client may depend on it.
        if self.module:
            out["module"] = self.module
        return out

    @staticmethod
    def from_json(raw: dict[str, Any]) -> Endpoint:
        return Endpoint(
            method=raw["method"],
            path=raw["path"],
            handler=raw["handler"],
            module=raw.get("module", ""),
            deps=list(raw.get("deps", [])),
            auth=bool(raw.get("auth", False)),
            model=raw.get("model", ""),
            kind=raw.get("kind", "custom"),
            summary=raw.get("summary", ""),
            request=BodySchema.from_json(raw["request"]) if raw.get("request") else None,
            response=BodySchema.from_json(raw["response"]) if raw.get("response") else None,
        )


@dataclass(slots=True)
class Page:
    """A human-facing HTML route serving a static shell out of static/pages.

    Kept in its own table because a page is not part of the API surface a
    native client consumes — see docs/DECISIONS.md § 13. `file` is the shell's
    base name and is the only thing that reaches the filesystem, never a value
    from a request.
    """

    path: str
    file: str
    title: str = ""
    auth: bool = False

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"path": self.path, "file": self.file}
        if self.title:
            out["title"] = self.title
        out["auth"] = self.auth
        return out

    @staticmethod
    def from_json(raw: dict[str, Any]) -> Page:
        return Page(
            path=raw["path"],
            file=raw["file"],
            title=raw.get("title", ""),
            auth=bool(raw.get("auth", False)),
        )


@dataclass(slots=True)
class Manifest:
    api_version: str = API_VERSION
    models: list[Model] = dataclass_field(default_factory=list)
    endpoints: list[Endpoint] = dataclass_field(default_factory=list)
    pages: list[Page] = dataclass_field(default_factory=list)

    # --- upserts ----------------------------------------------------------

    def upsert_model(self, model: Model) -> None:
        for index, existing in enumerate(self.models):
            if existing.name == model.name:
                self.models[index] = model
                return
        self.models.append(model)

    def upsert_endpoint(self, endpoint: Endpoint) -> None:
        """Replace a same-key endpoint or append.

        A same (method, path) naming a different handler is two scaffolds
        claiming one route. It raises and leaves the manifest untouched.
        """
        for index, existing in enumerate(self.endpoints):
            if existing.method == endpoint.method and existing.path == endpoint.path:
                if existing.handler != endpoint.handler:
                    raise ManifestError(
                        f"route conflict: {endpoint.method} {endpoint.path} is already "
                        f"registered by handler {existing.handler!r}, cannot reassign to "
                        f"{endpoint.handler!r}"
                    )
                self.endpoints[index] = endpoint
                return
        self.endpoints.append(endpoint)

    def upsert_page(self, page: Page) -> None:
        """Same path + same file refreshes the row; same path + a different
        file is two scaffolds claiming one URL."""
        for index, existing in enumerate(self.pages):
            if existing.path == page.path:
                if existing.file != page.file:
                    raise ManifestError(
                        f"page conflict: {page.path} is already served by "
                        f"static/pages/{existing.file}.html, cannot reassign to "
                        f"{page.file}.html"
                    )
                self.pages[index] = page
                return
        self.pages.append(page)

    # --- serialization ----------------------------------------------------

    def canonicalize(self) -> None:
        """Sort every table, so an otherwise-identical manifest always hashes
        the same regardless of the order things were scaffolded in."""
        self.models.sort(key=lambda m: m.name)
        self.endpoints.sort(key=lambda e: (e.path, e.method))
        self.pages.sort(key=lambda p: p.path)

    def payload(self) -> dict[str, Any]:
        return {
            "models": [m.to_json() for m in self.models],
            "endpoints": [e.to_json() for e in self.endpoints],
            "pages": [p.to_json() for p in self.pages],
        }

    def hash(self) -> str:
        """sha256 over the models, endpoints and pages — excluding generated_at,
        so an otherwise-identical manifest always hashes the same. Pages are in
        the payload so that adding or moving one shows as a surface change."""
        blob = json.dumps(self.payload(), separators=(",", ":"), sort_keys=False)
        return "sha256:" + hashlib.sha256(blob.encode()).hexdigest()

    def to_json(self, now: datetime) -> dict[str, Any]:
        self.canonicalize()
        return {
            "api_version": self.api_version or API_VERSION,
            "hash": self.hash(),
            "generated_at": now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "builder_version": BUILDER_VERSION,
            **self.payload(),
        }

    @staticmethod
    def from_json(raw: dict[str, Any]) -> Manifest:
        return Manifest(
            api_version=raw.get("api_version") or API_VERSION,
            models=[Model.from_json(m) for m in raw.get("models", [])],
            endpoints=[Endpoint.from_json(e) for e in raw.get("endpoints", [])],
            pages=[Page.from_json(p) for p in raw.get("pages", [])],
        )


def read_manifest(path: Path) -> Manifest:
    """A missing file is not an error — it is the empty manifest, the correct
    state for an app that has scaffolded nothing yet."""
    if not path.exists():
        return Manifest()
    try:
        return Manifest.from_json(json.loads(path.read_text()))
    except json.JSONDecodeError as exc:
        raise ManifestError(f"api.json is corrupt: {exc}") from exc


def write_manifest(path: Path, manifest: Manifest, now: datetime) -> None:
    """Atomic replace: a reader — `bb inspect`, gova-ios's export script, a
    human — sees either the previous complete file or this one, never a
    truncation from a crash mid-write."""
    atomic_write_json(path, manifest.to_json(now))


def fields_to_model(name: str, table: str, fields: list[Field]) -> Model:
    """Convert Field records into a manifest Model, adding the implicit `id`
    (first) and `created_at` (last) columns every generated table has."""
    out = [ModelField(name="id", type="int")]
    out.extend(
        ModelField(name=f.name, type=f.type, nullable=f.nullable, format=f.format, references=f.ref)
        for f in fields
    )
    out.append(ModelField(name="created_at", type="timestamp"))
    return Model(name=name, table=table, fields=out)


def resource_endpoints(model: Model, *, auth: bool = True) -> list[Endpoint]:
    """The five CRUD endpoints a resource registers. The handler symbols must
    match resource_handlers.py.j2 exactly.

    `auth` defaults to True. Generic CRUD includes create, update and delete,
    so an unguarded resource is world-writable data — and in an agent-driven
    template, whatever the default is, is what ships.
    """
    base = f"/api/v1/{to_plural(model.name)}"
    module = f"{model.name}_resource"

    def make(method: str, path: str, handler: str, kind: str) -> Endpoint:
        return Endpoint(
            method=method,
            path=path,
            handler=handler,
            module=module,
            deps=["db", "cache"],
            auth=auth,
            model=model.name,
            kind=kind,
            request=resource_request(model, kind),
            response=resource_response(model, kind),
        )

    name = model.name
    return [
        make("GET", base, f"{name}_list", "list"),
        make("GET", f"{base}/{{id}}", f"{name}_detail", "detail"),
        make("POST", base, f"{name}_create", "create"),
        make("PUT", f"{base}/{{id}}", f"{name}_update", "update"),
        make("DELETE", f"{base}/{{id}}", f"{name}_delete", "delete"),
    ]


def _writable_fields(model: Model) -> list[ModelField]:
    """Everything a client may send: not the implicit id or created_at."""
    return [f for f in model.fields if f.name not in ("id", "created_at")]


def resource_request(model: Model, kind: str) -> BodySchema | None:
    if kind in ("create", "update"):
        return BodySchema(shape="object", fields=_writable_fields(model))
    return None


def resource_response(model: Model, kind: str) -> BodySchema:
    if kind == "list":
        return BodySchema(shape="list", model=model.name)
    if kind == "delete":
        return BodySchema(shape="object", fields=[ModelField(name="status", type="string")])
    return BodySchema(shape="object", model=model.name)


def list_page(name: str, title: str, *, auth: bool = True) -> Page:
    """The page row a resource registers for its list shell.

    Matches its endpoints: a guarded resource gets a guarded page, so a
    signed-out visitor is redirected rather than shown a shell that 401s.
    """
    return Page(path=f"/{to_plural(name)}", file=to_plural(name), title=title, auth=auth)
