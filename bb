#!/usr/bin/env bash
# bb — the black-box application builder.
#
# A thin wrapper around the CLI in the `builder` container, which holds the
# installed package, its baked-in templates, and the bind mounts for /src and
# /data. Run it from anywhere in the repo.
#
#   ./bb inspect
#   ./bb sql -query "CREATE TABLE projects (...)"
#   ./bb resource -name project -fields name:string,status:string
#   ./bb help
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
exec docker compose exec -T builder bb "$@"
