"""End-to-end tests for the six commands, driven through `run` rather than a
subprocess — which is what makes the command layer testable at all."""

from __future__ import annotations

import json

import pytest

from bb.cli import run
from bb.manifest import read_manifest
from bb.tools import ToolError, Workspace
from conftest import PROJECTS_TABLE


def seed(ws: Workspace) -> None:
    run(["sql", "-query", PROJECTS_TABLE], ws)


# --- inspect --------------------------------------------------------------


def test_inspect_reports_the_committed_manifest(ws: Workspace) -> None:
    report = json.loads(run(["inspect"], ws))
    assert report["models"] == ["user"]
    assert "GET /api/v1/auth/me" in report["endpoints"]
    assert report["pages"] == ["/login", "/register"]


def test_inspect_on_an_empty_workspace_is_not_an_error(empty_ws: Workspace) -> None:
    """A missing manifest is the empty manifest, not a failure."""
    report = json.loads(run(["inspect"], empty_ws))
    assert report["models"] == []


def test_inspect_reports_divergence(ws: Workspace) -> None:
    (ws.pages_dir / "login.html").unlink()
    report = json.loads(run(["inspect"], ws))
    assert report["divergence"]["registered_pages_without_a_shell"] == ["/login"]


# --- sql ------------------------------------------------------------------


def test_sql_creates_a_table(ws: Workspace) -> None:
    assert "successfully" in run(["sql", "-query", PROJECTS_TABLE], ws)


def test_sql_reports_a_syntax_error(ws: Workspace) -> None:
    with pytest.raises(ToolError):
        run(["sql", "-query", "CREATE TABL oops ("], ws)


# --- model ----------------------------------------------------------------


def test_model_writes_files_and_registers(ws: Workspace) -> None:
    seed(ws)
    out = run(["model", "-name", "project", "-fields", "name:string,status:string"], ws)
    assert "Registered model project" in out
    assert (ws.models_dir / "project.py").is_file()
    assert (ws.models_dir / "test_project.py").is_file()
    assert "project" in {m.name for m in read_manifest(ws.manifest_path).models}


def test_model_registers_no_route(ws: Workspace) -> None:
    seed(ws)
    before = len(read_manifest(ws.manifest_path).endpoints)
    run(["model", "-name", "project", "-fields", "name:string"], ws)
    assert len(read_manifest(ws.manifest_path).endpoints) == before


def test_model_requires_the_table_to_exist(ws: Workspace) -> None:
    with pytest.raises(ToolError, match="does not exist"):
        run(["model", "-name", "project", "-fields", "name:string"], ws)


def test_model_refuses_a_reserved_name(ws: Workspace) -> None:
    with pytest.raises(ToolError, match="reserved"):
        run(["model", "-name", "user", "-fields", "name:string"], ws)


def test_model_refuses_a_name_that_shadows_the_stdlib(ws: Workspace) -> None:
    """models/ is a plain package directory, so `json.py` there would shadow
    the real json for every generated import."""
    with pytest.raises(ToolError, match="shadow"):
        run(["model", "-name", "json", "-fields", "name:string"], ws)


def test_model_refuses_a_credential_column(ws: Workspace) -> None:
    """Generic CRUD is a shape where a request that omits a field overwrites
    it — a credential column reachable from one is a credential anybody can
    blank."""
    run(
        [
            "sql",
            "-query",
            "CREATE TABLE secrets_tables (id INTEGER PRIMARY KEY, api_secret TEXT, "
            "created_at DATETIME DEFAULT CURRENT_TIMESTAMP);",
        ],
        ws,
    )
    with pytest.raises(ToolError, match="credential"):
        run(["model", "-name", "secrets_table", "-fields", "api_secret:string"], ws)


def test_model_refuses_an_unknown_field_type(ws: Workspace) -> None:
    seed(ws)
    with pytest.raises(ToolError, match="unknown type"):
        run(["model", "-name", "project", "-fields", "name:strng"], ws)


def test_model_refuses_a_type_that_disagrees_with_the_column(ws: Workspace) -> None:
    seed(ws)
    with pytest.raises(ToolError, match="declared as int"):
        run(["model", "-name", "project", "-fields", "name:int"], ws)


def test_model_requires_id_and_created_at(ws: Workspace) -> None:
    run(["sql", "-query", "CREATE TABLE notes (body TEXT);"], ws)
    with pytest.raises(ToolError, match='no "id" column'):
        run(["model", "-name", "note", "-fields", "body:string"], ws)


# --- page -----------------------------------------------------------------


def test_page_writes_a_shell_and_a_module(ws: Workspace) -> None:
    run(["page", "-file", "dashboard", "-title", "Dashboard", "-path", "/dashboard"], ws)
    assert (ws.pages_dir / "dashboard.html").is_file()
    assert (ws.js_dir / "dashboard.js").is_file()
    assert "/dashboard" in {p.path for p in read_manifest(ws.manifest_path).pages}


def test_page_refuses_the_api_namespace(ws: Workspace) -> None:
    """The exact inverse of the handler check — that is what keeps the two
    manifest tables provably disjoint."""
    with pytest.raises(ToolError, match="must not be under /api/"):
        run(["page", "-file", "x", "-title", "X", "-path", "/api/v1/x"], ws)


def test_page_refuses_the_static_namespace(ws: Workspace) -> None:
    with pytest.raises(ToolError, match="/static/"):
        run(["page", "-file", "x", "-title", "X", "-path", "/static/x"], ws)


def test_guarded_page_generates_a_redirect_guard(ws: Workspace) -> None:
    run(["page", "-file", "secret", "-title", "Secret", "-path", "/secret", "-auth"], ws)
    generated = (ws.handlers_dir / "pages_gen.py").read_text()
    assert "require_page_auth" in generated


# --- handler --------------------------------------------------------------


def test_handler_writes_a_stub_and_registers(ws: Workspace) -> None:
    run(["handler", "-name", "search", "-method", "POST", "-path", "/api/v1/search"], ws)
    assert (ws.handlers_dir / "search.py").is_file()
    routes = (ws.handlers_dir / "routes_gen.py").read_text()
    assert "/api/v1/search" in routes


def test_handler_requires_the_api_prefix(ws: Workspace) -> None:
    with pytest.raises(ToolError, match="/api/v1/"):
        run(["handler", "-name", "x", "-method", "GET", "-path", "/x"], ws)


def test_handler_refuses_an_unsupported_method(ws: Workspace) -> None:
    with pytest.raises(ToolError, match="-method must be"):
        run(["handler", "-name", "x", "-method", "PATCH", "-path", "/api/v1/x"], ws)


def test_handler_auth_flag_wraps_the_route(ws: Workspace) -> None:
    run(["handler", "-name", "x", "-method", "GET", "-path", "/api/v1/x", "-auth"], ws)
    assert "require_auth" in (ws.handlers_dir / "routes_gen.py").read_text()


def test_handler_rejects_a_malformed_schema(ws: Workspace) -> None:
    with pytest.raises(ToolError, match="not valid JSON"):
        run(
            [
                "handler",
                "-name",
                "x",
                "-method",
                "GET",
                "-path",
                "/api/v1/x",
                "-request-schema",
                "{oops",
            ],
            ws,
        )


# --- resource -------------------------------------------------------------


def test_resource_writes_six_files_and_registers_everything(ws: Workspace) -> None:
    seed(ws)
    run(["resource", "-name", "project", "-fields", "name:string,status:string"], ws)
    for relative in (
        "models/project.py",
        "models/test_project.py",
        "handlers/project_resource.py",
        "handlers/test_project_resource.py",
        "static/pages/projects.html",
        "static/js/projects.js",
    ):
        assert (ws.app_dir / relative).is_file(), relative

    manifest = read_manifest(ws.manifest_path)
    paths = {(e.method, e.path) for e in manifest.endpoints}
    assert ("GET", "/api/v1/projects") in paths
    assert ("GET", "/api/v1/projects/{id}") in paths
    assert ("POST", "/api/v1/projects") in paths
    assert ("PUT", "/api/v1/projects/{id}") in paths
    assert ("DELETE", "/api/v1/projects/{id}") in paths
    assert "/projects" in {p.path for p in manifest.pages}


def test_resource_pages_are_plural_so_they_cannot_collide_with_auth(ws: Workspace) -> None:
    """to_plural never returns its input unchanged, so a resource named `login`
    lands at /logins."""
    run(
        [
            "sql",
            "-query",
            "CREATE TABLE logins (id INTEGER PRIMARY KEY, name TEXT NOT NULL, "
            "created_at DATETIME DEFAULT CURRENT_TIMESTAMP);",
        ],
        ws,
    )
    run(["resource", "-name", "login", "-fields", "name:string"], ws)
    paths = {p.path for p in read_manifest(ws.manifest_path).pages}
    assert "/logins" in paths
    assert "/login" in paths  # the auth page survived


def test_scaffolding_twice_is_idempotent(ws: Workspace) -> None:
    seed(ws)
    run(["resource", "-name", "project", "-fields", "name:string,status:string"], ws)
    first = read_manifest(ws.manifest_path).hash()
    run(["resource", "-name", "project", "-fields", "name:string,status:string"], ws)
    assert read_manifest(ws.manifest_path).hash() == first


# --- misc -----------------------------------------------------------------


def test_version_prints_the_builder_version(ws: Workspace) -> None:
    assert run(["version"], ws).count(".") >= 1


def test_help_lists_the_commands(ws: Workspace) -> None:
    out = run([], ws)
    for command in ("inspect", "sql", "model", "page", "handler", "resource"):
        assert command in out


def test_a_format_hint_mismatch_explains_itself_in_the_callers_words(ws: Workspace) -> None:
    """`due_at:datetime` resolves to a string with a format hint, so an error
    saying only "declared as string" describes something nobody typed."""
    run(
        [
            "sql",
            "-query",
            "CREATE TABLE events (id INTEGER PRIMARY KEY, at DATETIME, "
            "created_at DATETIME DEFAULT CURRENT_TIMESTAMP);",
        ],
        ws,
    )
    with pytest.raises(ToolError) as caught:
        run(["model", "-name", "event", "-fields", "at:datetime"], ws)
    message = str(caught.value)
    assert "declared as datetime" in message
    assert "at:timestamp" in message


# --- body schemas are validated, not trusted -----------------------------


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ("{oops", "not valid JSON"),
        ('["a"]', "must be a JSON object"),
        ('{"shape": "nope"}', "shape must be"),
        ('{"model": 123}', "model must be a string"),
        ('{"fields": "nope"}', "fields must be a list"),
        ('{"fields": ["nope"]}', "each entry in fields must be an object"),
        ('{"fields": [{"type": "string"}]}', "string 'name'"),
        ('{"fields": [{"name": "x", "type": 7}]}', "string 'type'"),
        ('{"model": "user", "fields": [{"name": "x", "type": "string"}]}', "never both"),
    ],
)
def test_a_bad_body_schema_is_refused(ws: Workspace, schema: str, expected: str) -> None:
    """Whatever survives this goes verbatim into api.json, which a native
    client then reads — so a wrong type here would be persisted, not merely
    rejected."""
    with pytest.raises(ToolError, match=expected):
        run(
            [
                "handler",
                "-name",
                "x",
                "-method",
                "GET",
                "-path",
                "/api/v1/x",
                "-request-schema",
                schema,
            ],
            ws,
        )


def test_a_valid_body_schema_is_recorded(ws: Workspace) -> None:
    run(
        [
            "handler",
            "-name",
            "search",
            "-method",
            "POST",
            "-path",
            "/api/v1/search",
            "-request-schema",
            '{"shape": "object", "fields": [{"name": "q", "type": "string"}]}',
        ],
        ws,
    )
    endpoint = next(
        e for e in read_manifest(ws.manifest_path).endpoints if e.path == "/api/v1/search"
    )
    assert endpoint.request is not None
    assert [f.name for f in endpoint.request.fields] == ["q"]
