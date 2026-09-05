#!/bin/sh
set -e

cd /src/app
/usr/local/bin/tailwindcss -i ./static/css/input.css -o ./static/css/style.css --minify

# One worker, and deliberately no --reload. The in-process cache and the
# single-writer SQLite pool assume one process, and a reloader watching a tree
# that an agent is mid-write in produces crash loops rather than convenience.
# See docs/DECISIONS.md § 8.
exec uvicorn main:app --host 0.0.0.0 --port "${APP_PORT:-8080}"
