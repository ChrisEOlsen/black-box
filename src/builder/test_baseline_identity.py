"""The committed generated files must be exactly what the builder produces.

src/app ships with routes_gen.py, pages_gen.py and test_pages_gen.py already
written, because the app has to run before the builder exists. That is a
circularity, and this is what closes it: if the generator and the committed
baseline ever disagree, the next scaffold silently rewrites files nobody
reviewed. See docs/DECISIONS.md § 14.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bb.codegen import pages_data, routes_data
from bb.manifest import read_manifest
from bb.render import format_python, render_to_string

APP = Path(__file__).resolve().parents[1] / "app"

CASES = [
    ("routes_gen.py", "routes_gen.py.j2", routes_data),
    ("pages_gen.py", "pages_gen.py.j2", pages_data),
    ("test_pages_gen.py", "test_pages_gen.py.j2", pages_data),
]


@pytest.mark.parametrize(("filename", "template", "build_data"), CASES)
def test_committed_file_matches_a_fresh_render(
    filename: str, template: str, build_data: object
) -> None:
    manifest = read_manifest(APP / "api.json")
    data = build_data(manifest)  # type: ignore[operator]
    target = APP / "handlers" / filename
    generated = format_python(str(target), render_to_string(template, data))
    assert generated == target.read_text(), (
        f"{filename} differs from what the builder would write. "
        "Regenerate it rather than hand-editing."
    )


def test_the_manifest_hash_matches_its_contents() -> None:
    """A stale hash means somebody hand-edited api.json."""
    manifest_path = APP / "api.json"
    recorded = json.loads(manifest_path.read_text())["hash"]
    assert read_manifest(manifest_path).hash() == recorded
