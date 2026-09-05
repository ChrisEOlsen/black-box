# black-box

A template for building web apps with an AI assistant.

**Python · FastAPI · Vanilla JS · SQLite**

## What it is

Clone it and you already have a working web app: a FastAPI server, a database,
and a complete sign-in system — registration, login, sessions, password hashing,
rate limiting, CSRF. None of that is something you ask for; it is committed code.

For everything else there is a small CLI called `bb`. You give it a table and it
writes the feature:

```bash
./bb sql -query "CREATE TABLE projects (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    status     TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);"

./bb resource -name project -fields name:string,status:string
```

That second command writes the database code, the API endpoints, a web page with
a form and delete buttons, and tests for all of it — then wires up the routes.
The AI customizes what it generated rather than writing it from scratch.

The point: **the AI decides what to build; templates decide how.** Generated
code arrives already wired, already tested, and already following the security
rules, so a feature costs about a thousand tokens instead of a thousand lines of
guesswork.

## Getting started

```bash
cp env.example .env          # set APP_NAME and SESSION_SECRET
./install-claude.sh          # or ./install-opencode.sh, or both
```

Then:

1. Describe your app in `SEED.md`
2. Run `/build` — the assistant designs it, plans it, and builds it
3. Open `http://localhost:8080`
4. Run `/launch` to put it online through a Cloudflare Tunnel

## The commands

Run `./bb help` for details.

| Command | What it makes |
|---|---|
| `bb inspect` | what exists right now, and anything out of sync |
| `bb sql` | a table |
| `bb model` | database code for a table |
| `bb handler` | one custom API endpoint |
| `bb page` | a web page (HTML + JS) |
| `bb resource` | the whole thing: data, API, page, form, tests |

## How it fits together

**One file describes the app.** `src/app/api.json` lists every data model, API
endpoint and page. The CLI writes it, and the server's routing is generated from
it — so nobody hand-wires a route.

**Two containers.** `app` runs the server; `builder` holds the `bb` CLI. One
SQLite file underneath. No Redis, no Nginx, no frontend build step.

**Plain everything.** FastAPI returns JSON. Vanilla ES modules render the page.
Tailwind does the styling. No ORM, no framework on the front end, no bundler,
no Node.

## Verifying

Python has no compile step, so one script stands in for it:

```bash
scripts/verify          # restart, then ruff + ruff format + mypy --strict + pytest
scripts/verify --local  # same checks against ./.venv, no Docker
```

All four, every time. That is what replaces `go build`.

## Relationship to gova-monolith

black-box is a port of [`gova-monolith`](../gova-monolith) (Go · chi · SQLite ·
vanilla JS). The two share `docs/API-CONTRACT.md` **byte for byte**: same
response envelope, same error codes, same timestamp format, same `api.json`
schema.

That is deliberate. [`gova-ios`](../gova-ios) reads `$WEB_APP/src/app/api.json`
off disk and does not care which backend wrote it, so **one iOS app works
against either template**. Pick the backend language; keep the client.

## Reference

- [`CLAUDE.md`](CLAUDE.md) — the rules the assistant works under
- [`docs/API-CONTRACT.md`](docs/API-CONTRACT.md) — what the API guarantees (frozen)
- [`docs/DECISIONS.md`](docs/DECISIONS.md) — why the tricky parts are built the way they are

## Stack

| Layer | Choice |
|---|---|
| Language | Python 3.13 |
| Framework | FastAPI |
| Server | uvicorn, one worker |
| Frontend | Vanilla ES modules |
| Database | SQLite (WAL), stdlib `sqlite3` |
| Schemas | Pydantic v2 |
| CSS | Tailwind CLI |
| Auth | Signed cookies + bearer tokens |
| Gate | ruff · mypy --strict · pytest |
| Deploy | Cloudflare Tunnel |
