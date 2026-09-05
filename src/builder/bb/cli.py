"""bb — the black-box application builder.

Single-dash flags, matching gova-monolith's `flag` package spelling, so every
command in the docs and skills is copy-pasteable across both templates.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from bb.fields import FieldError, split_fields
from bb.manifest import BUILDER_VERSION, ManifestError
from bb.schema import SchemaError
from bb.tools import (
    ToolError,
    Workspace,
    create_handler,
    create_model,
    create_page,
    default_workspace,
    execute_sql,
    inspect,
    scaffold_resource,
)

USAGE = """bb — the black-box application builder.

Usage:
  bb <command> [flags]

Commands:
  inspect     Show the api.json manifest, the files on disk, and any divergence.
              Run this first.
  sql         Execute SQL against the app database. Run before creating a model.
  model       Generate a model for an existing table. Registers it in api.json;
              creates no route.
  page        Generate an HTML shell + JS module at a human-facing URL.
  handler     Generate one custom JSON endpoint under /api/v1/ and register it.
  resource    Generate a full CRUD resource: model, five handlers, and a page
              with a create form and delete buttons.
  version     Print the builder version.

Field syntax (model, resource):
  Comma-separated name:type pairs — "title:string,quantity:int,due_at:timestamp"
  Types: string, int, boolean, float, timestamp.
  A DATETIME column must be declared timestamp, never string.
  name:ref:<model> is a foreign key; name:email|date|datetime|time|json is a
  string with a format hint.

Run "bb <command> -h" for a command's flags.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bb", add_help=False)
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("inspect", add_help=True)

    sql = sub.add_parser("sql", add_help=True)
    sql.add_argument("-query", required=True, help="SQL to execute")

    model = sub.add_parser("model", add_help=True)
    model.add_argument("-name", required=True, help="model name, snake_case singular")
    model.add_argument("-fields", required=True, help="comma-separated name:type list")

    page = sub.add_parser("page", add_help=True)
    page.add_argument("-file", required=True, help="filename without extension")
    page.add_argument("-title", required=True, help="page title")
    page.add_argument("-path", required=True, help="human-facing URL, e.g. /dashboard")
    page.add_argument("-auth", action="store_true", help="redirect signed-out visitors to /login")

    handler = sub.add_parser("handler", add_help=True)
    handler.add_argument("-name", required=True, help="handler name, snake_case")
    handler.add_argument("-method", required=True, help="GET, POST, PUT or DELETE")
    handler.add_argument("-path", required=True, help="full route path under /api/v1/")
    handler.add_argument("-auth", action="store_true", help="require a signed-in user")
    handler.add_argument("-summary", default="", help="one line describing the endpoint")
    handler.add_argument("-request-schema", dest="request_schema", default="")
    handler.add_argument("-response-schema", dest="response_schema", default="")

    resource = sub.add_parser("resource", add_help=True)
    resource.add_argument("-name", required=True, help="resource name, snake_case singular")
    resource.add_argument("-fields", required=True, help="comma-separated name:type list")

    sub.add_parser("version", add_help=True)
    return parser


def run(argv: Sequence[str], ws: Workspace | None = None) -> str:
    """Dispatch one subcommand. Split out from main so tests can drive the
    whole CLI surface without a subprocess."""
    if not argv or argv[0] in ("help", "-h", "--help"):
        return USAGE.rstrip("\n")

    workspace = ws or default_workspace()
    args = build_parser().parse_args(list(argv))

    match args.command:
        case "inspect":
            return inspect(workspace)
        case "sql":
            return execute_sql(workspace, args.query)
        case "model":
            return create_model(workspace, args.name, split_fields(args.fields))
        case "page":
            return create_page(workspace, args.file, args.title, args.path, auth=args.auth)
        case "handler":
            return create_handler(
                workspace,
                args.name,
                args.method,
                args.path,
                auth=args.auth,
                summary=args.summary,
                request_schema=args.request_schema,
                response_schema=args.response_schema,
            )
        case "resource":
            return scaffold_resource(workspace, args.name, split_fields(args.fields))
        case "version":
            return BUILDER_VERSION
        case _:
            raise ToolError('unknown command — run "bb help" for the list')


def main() -> int:
    argv = sys.argv[1:]
    if not argv:
        sys.stderr.write(USAGE)
        return 2
    try:
        out = run(argv)
    except (ToolError, SchemaError, ManifestError, FieldError) as exc:
        sys.stderr.write(f"bb: {exc}\n")
        return 1
    if out:
        sys.stdout.write(out + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
