"""Template rendering, and the formatting pass that makes it survivable.

Python is whitespace-sensitive, so hand-aligning a Jinja2 `{% for %}` against
output of unknown length is the largest authoring risk in this generator. The
templates render loosely and `ruff format` fixes the layout afterwards — the
same trade gova-monolith makes with gofmt, for the same reason, but load-bearing
here rather than merely tidy. See docs/DECISIONS.md § 2.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from collections.abc import Callable, MutableMapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from bb.fields import (
    Field,
    field_annotation,
    html_input_type,
    python_type,
    sql_type,
    test_json_literal,
    test_literal,
    title_case,
    to_pascal,
    to_plural,
)
from bb.lock import atomic_write

log = logging.getLogger("bb")

TEMPLATES_DIR = Path(__file__).parent / "templates"

# How long `ruff format` may take on one generated file before we give up and
# write it unformatted.
FORMAT_TIMEOUT_SECONDS = 30


@dataclass(slots=True)
class TemplateData:
    """What every file template renders from."""

    name: str = ""
    pascal_name: str = ""
    plural_name: str = ""
    pascal_plural: str = ""
    fields: list[Field] = field(default_factory=list)
    auth_required: bool = False
    method: str = ""
    title: str = ""
    crud: bool = False

    @property
    def has_writable_timestamp(self) -> bool:
        """Drives a conditional import: an unused one fails `ruff check`."""
        return any(f.type == "timestamp" for f in self.fields)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "pascal_name": self.pascal_name,
            "plural_name": self.plural_name,
            "pascal_plural": self.pascal_plural,
            "fields": self.fields,
            "auth_required": self.auth_required,
            "method": self.method,
            "title": self.title,
            "crud": self.crud,
            "has_writable_timestamp": self.has_writable_timestamp,
        }


def new_data(name: str, fields: list[Field] | None = None) -> TemplateData:
    return TemplateData(
        name=name,
        pascal_name=to_pascal(name),
        plural_name=to_plural(name),
        pascal_plural=to_pascal(to_plural(name)),
        fields=fields or [],
    )


def _join_names(fields: list[Field]) -> str:
    return ", ".join(f.name for f in fields)


def _placeholders(fields: list[Field]) -> str:
    return ", ".join("?" for _ in fields)


def _update_set(fields: list[Field]) -> str:
    return ", ".join(f"{f.name} = ?" for f in fields)


def _signature_params(fields: list[Field]) -> str:
    """The typed parameter list a generated Create/Update takes."""
    return ", ".join(f"{f.name}: {field_annotation(f)}" for f in fields)


def _bind_args(fields: list[Field], prefix: str) -> str:
    """The bound parameters for an INSERT or UPDATE.

    Always trailing-comma terminated, so a single-field tuple is still a tuple
    and a caller can append `item_id` after it. A timestamp goes through
    timestamp_to_db, because sqlite3 will not bind a datetime.
    """
    parts: list[str] = []
    for f in fields:
        expr = f"{prefix}{f.name}"
        if f.type == "timestamp":
            expr = f"timestamp_to_db({expr})"
        parts.append(expr)
    return "".join(f"{part}, " for part in parts)


def build_environment() -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        # StrictUndefined so a typo in a template is an error at render time,
        # not a silently empty string in generated code.
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
        # HTML output is escaped; .py and .js templates are not, because
        # escaping would corrupt the source they emit. Values reaching a
        # template come from a developer's own CLI invocation, never from a
        # request, but a page title still lands in markup and gets escaped.
        autoescape=select_autoescape(enabled_extensions=("html",), default=False),
    )
    # Jinja's stubs enumerate only its own builtin filters, so a custom filter
    # whose signature matches none of them fails the union check. Registering
    # filters is documented public API; this is a stub limitation, not a defect.
    filters = cast("MutableMapping[str, Callable[..., object]]", env.filters)
    filters.update(
        {
            "pascal": to_pascal,
            "plural": to_plural,
            "title_case": title_case,
            "py_type": python_type,
            "annotation": field_annotation,
            "sql_type": sql_type,
            "input_type": html_input_type,
            "test_literal": test_literal,
            "test_json": test_json_literal,
            "join_names": _join_names,
            "placeholders": _placeholders,
            "update_set": _update_set,
            "signature_params": _signature_params,
            "bind_args": _bind_args,
        }
    )
    return env


def render_to_string(template_name: str, data: dict[str, Any]) -> str:
    return build_environment().get_template(template_name).render(**data)


def find_ruff() -> str | None:
    """Locate the ruff binary.

    Beside the running interpreter first, so a venv-installed ruff is found
    when the builder runs from a checkout; PATH second, which is how it
    resolves inside the builder image.
    """
    beside = Path(sys.executable).parent / "ruff"
    if beside.is_file():
        return str(beside)
    return shutil.which("ruff")


def _run_ruff(ruff: str, args: list[str], name: str, source: str) -> str | None:
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [ruff, *args, "--stdin-filename", name, "-"],
            input=source,
            capture_output=True,
            text=True,
            timeout=FORMAT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("bb: could not run ruff on %s: %s", name, exc)
        return None
    if result.returncode not in (0, 1):
        log.warning(
            "bb: %s does not parse as Python, leaving it unformatted: %s",
            name,
            result.stderr.strip(),
        )
        return None
    return result.stdout


def format_python(name: str, source: str) -> str:
    """Sort a generated file's imports, then format it.

    Only import ordering is auto-fixed. An UNUSED import is deliberately left
    alone so it fails `ruff check` and sends the author back to the template's
    conditional — the same discipline gova-monolith's `AnyAuth` flag enforces
    through the Go compiler.

    A failure is NOT fatal: the unformatted bytes are written and the error
    surfaces at `ruff check` pointing at the real problem, rather than leaving
    the author an empty file and a message about formatting.
    """
    ruff = find_ruff()
    if ruff is None:  # pragma: no cover - ruff ships in the builder image
        log.warning("bb: ruff not found, leaving %s unformatted", name)
        return source

    sorted_source = _run_ruff(ruff, ["check", "--select", "I", "--fix", "-q"], name, source)
    if sorted_source is None:
        return source
    formatted = _run_ruff(ruff, ["format"], name, sorted_source)
    return source if formatted is None else formatted


def write_file(path: Path, source: str) -> None:
    """Write `source`, formatting .py output first.

    The FULL destination path is handed to ruff as the stdin filename, not just
    the basename: that is how ruff discovers the app's own pyproject.toml, and
    therefore its line length and its known-first-party list. With a bare name
    it resolves config from the builder's directory instead and sorts
    `handlers`/`models` as third-party.

    Atomic because parallel agents read this tree while it is being written.
    """
    if path.suffix == ".py":
        source = format_python(str(path), source)
    atomic_write(path, source)


def render_to_file(template_name: str, path: Path, data: dict[str, Any]) -> None:
    write_file(path, render_to_string(template_name, data))
