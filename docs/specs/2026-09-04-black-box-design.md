# black-box — Design

> A template for building web apps with an AI assistant.
> **Python · FastAPI · Vanilla JS · SQLite**
>
> Status: approved design, pre-implementation. Source template:
> [`gova-monolith`](../../../gova-monolith) (Go · chi · vanilla JS · SQLite).

## 1. Goal

Reproduce gova-monolith's working model on a Python/FastAPI backend, keeping the
wire contract byte-identical so the `gova-ios` companion template works against
either backend unchanged.

This is a port of a *method*, not of a codebase. The stack is the least
interesting part of gova-monolith. What is being ported are six ideas:

1. **`src/app/api.json` is the source of truth** for the served surface — models,
   endpoints, pages. Routes are generated from it and never hand-wired.
2. **A CLI generates the feature; the agent customizes it.** Scaffolding is
   mandatory for feature code; infrastructure is hand-written; the boundary is
   explicit and enforced by rule.
3. **Auth is committed code, not a scaffold.** It ships in `src/app`, the
   verification gate covers it, and the generator refuses credential columns.
4. **Two containers, one lock.** `app` restarts freely; `builder` holds the
   generator so a restart cannot kill a scaffold mid-write. An flock wraps the
   whole read→upsert→write→regenerate transaction.
5. **A frozen wire contract**, so a second client can be generated from the
   same manifest.
6. **Orchestrator/implementer split** — subagents author code; the orchestrator
   owns the generator, restarts, the test suite, and git.

All six port. Exactly one of them is hard in Python; see §4.

## 2. Decisions

| # | Decision | Choice | Why |
|---|---|---|---|
| 1 | Codegen model | **Full parity** — the builder renders real `.py` files the agent then customizes | Preserves "generated code arrives already wired and already tested". A runtime-generic CRUD router would leave the agent nothing to customize and turn the template into a framework. |
| 2 | Database layer | **stdlib `sqlite3`**, sync `def` handlers | Direct port of `db/db.go`. No ORM to fight the codegen, no extra dependency, and no async/blocking footguns. SQLite has no I/O concurrency to win. |
| 3 | Wire contract | **Frozen and shared** with gova-monolith | `docs/API-CONTRACT.md` becomes a shared frozen document. Buys the iOS client for free and makes the two backends A/B-comparable. |
| 4 | Name / location | **black-box**, CLI `./bb`, in the existing `~/Desktop/repos/black-box` | — |
| 5 | Harnesses | **Claude Code + opencode**, same as GOVA | `AGENTS.md` symlink, `.opencode/agent/*.md` model tiers, symlinked commands, two install scripts. |
| 6 | API docs | **Self-hosted Swagger, local only** | Vendored assets under `static/`, route mounted only when `APP_ENV != production`. No CSP exception, no info leak on a deployed app. |
| 7 | Process | Spec → plan → build | This document is step one. |

## 3. Stack

| Layer | Choice | Notes |
|---|---|---|
| Language | Python 3.13 | |
| Framework | FastAPI + Starlette | |
| Server | uvicorn, **one worker** | The in-process cache and the single-writer pool assume one process, as the Go binary did. |
| Database | stdlib `sqlite3`, WAL | 1 write connection behind a `threading.Lock`; thread-local read connections. |
| Schemas | Pydantic v2 | Request bodies, row types, and a generic `Envelope[T]`. |
| Auth | hand-written: stdlib `hmac` signed cookie, `bcrypt`, SQLite-backed rate limiter | Not `passlib` — unmaintained and broken against bcrypt 4.x. |
| CSS | Tailwind standalone binary | Unchanged from GOVA. No Node. |
| Frontend | vanilla ES modules | `api.js` / `auth.js` copy over verbatim — the wire is identical. |
| Builder | Python, `argparse` + Jinja2 + `ruff format` | `argparse` mirrors Go's `flag` package. |
| Deps | `uv` + `pyproject.toml` + lockfile | |

**App runtime dependencies, complete:** `fastapi`, `uvicorn[standard]`,
`pydantic`, `bcrypt`. **Dev:** `pytest`, `httpx`, `ruff`, `mypy`.
**Builder:** `jinja2`, `ruff`.

Keeping that list this short is a design goal, not an accident — dependency
surface is this stack's specific risk (§9).

## 4. The central problem: there is no `go build`

Go's entire safety model in gova-monolith is that `go test ./...` compiles the
whole package. That is what makes it safe for three subagents to edit different
files without compiling, what catches a bad template render, and what
`TestRenderResourceToDir` exists to exercise (GOVA DECISIONS §10). Python has no
equivalent: a generator that emits broken Python fails at the first request, in
production, silently.

### The replacement gate

`scripts/verify` runs, in order:

```
docker compose restart app          # and wait for /api/v1/_version to answer
docker compose exec -T app ruff check .        # undefined names, bad signatures
docker compose exec -T app ruff format --check .
docker compose exec -T app mypy .              # strict
docker compose exec -T app pytest -q
```

| Go | black-box | Catches |
|---|---|---|
| `go build ./...` | `ruff check` | undefined names, unused imports, unreachable code, bad call signatures |
| type errors | `mypy --strict src/app` | wrong types across module boundaries |
| `go test ./...` | `pytest` | behavior |
| `gofmt` after render | `ruff format` after render | indentation of generated code |

`mypy --strict` is reachable *because* the generated code is fully annotated —
which is itself a reason to keep full-parity codegen rather than dynamic
dispatch.

### `ruff format` is load-bearing

Python is whitespace-sensitive, so hand-aligning Jinja2 `{% for %}` blocks
against output of unknown length is the single largest authoring risk in this
port. The builder renders loosely and formats after, exactly as `render.go`'s
`formatGo` does — and, like `formatGo`, a formatting failure is **not fatal**:
log it, write the unformatted bytes, and let the error surface at `ruff check`
pointing at the real problem rather than handing the author an empty file.

### The generator's own compile test

`TestRenderResourceToDir` ports as `test_render_resource_to_dir`: render a
resource into a scratch copy of `src/app`, then run `ruff check`, `mypy` and
`pytest` over the result. In Go this test caught three defects the parse tests
could not see. Here there are no parse tests worth having, so this is the only
thing standing between a bad template and a runtime 500. **It is the highest-value
test in the repo.**

## 5. Repo layout

Mirrors gova-monolith deliberately: same paths, same container split, so the
skills and commands port as text edits rather than rewrites.

```
black-box/
├── bb                          # wrapper → docker compose exec builder /usr/local/bin/bb
├── Dockerfile                  # base → app | builder-cli
├── docker-compose.yml          # app + builder, one SQLite file
├── entrypoint.sh               # tailwind compile, then uvicorn
├── env.example  .env
├── CLAUDE.md ← AGENTS.md (symlink)
├── SEED.md  README.md  CHECKLIST.md
├── install-common.sh  install-claude.sh  install-opencode.sh
├── docs/
│   ├── API-CONTRACT.md         # frozen; mirrors gova-monolith
│   ├── DECISIONS.md
│   └── specs/  plans/
├── .claude/
│   ├── commands/  build.md  launch.md  security/analyze.md
│   └── skills/  bb-brainstorm/  bb-writing-plans/  bb-build-execution/
├── .opencode/  agent/{bb-implementer,bb-reviewer,bb-architect}.md  command/ → ../.claude/commands
└── src/
    ├── .bb-lock
    ├── app/
    │   ├── api.json                    ← same path gova-ios reads
    │   ├── pyproject.toml  uv.lock
    │   ├── main.py
    │   ├── db/         database.py  schema.sql  testutil.py
    │   ├── cache/      cache.py
    │   ├── models/     timestamp.py  query.py  user.py  mobile_token.py  <generated>
    │   ├── handlers/   envelope.py  auth.py  auth_mobile.py  register.py
    │   │               ratelimit.py  clientip.py  paging.py  version.py  home.py
    │   │               routes_gen.py  pages_gen.py  <generated>
    │   ├── middleware/ auth.py  csrf.py  security.py
    │   └── static/     css/  js/  js/lib/  pages/  vendor/swagger/
    └── builder/
        ├── pyproject.toml
        └── bb/  cli.py  manifest.py  fields.py  schema.py  render.py
                 lock.py  inspect.py  tools.py  templates/*.j2
```

**Tests live alongside their code** as `test_<name>.py` — `handlers/test_project_resource.py`,
not a `tests/` tree. This preserves the property the execution skill depends on:
every file belongs to exactly one task, so a failing test names its own
implementer.

## 6. Design deltas

Where black-box deliberately differs from a literal transcription. Each of these
becomes an entry in `docs/DECISIONS.md`.

### 6.1 Handlers are typed functions, not factories

Go handlers are factories — `ProjectListGET(database, appCache)` returns a
closure. Ported literally this would be a disaster: FastAPI reads the
**signature** of the endpoint function to derive path params, query params, body
validation and OpenAPI. A closure taking `Request` discards all of it.

Generated handlers are therefore plain module-level functions:

```python
def project_create(body: ProjectRequest, db: DatabaseDep, cache: CacheDep) -> Envelope[Project]: ...
```

with `DatabaseDep = Annotated[Database, Depends(get_db)]`.

Three consequences:

- **`deps` in the manifest becomes vestigial.** FastAPI's DI does what Go's
  `callExpr` did by hand. `deps` is already documented as not part of the
  contract, so it stays in the file for wire-compat and the generator ignores it
  on read.
- **`routes_gen.py` is simpler than `routes_gen.go`** — one
  `app.add_api_route(path, fn, methods=[...], response_model=..., dependencies=[...])`
  per endpoint. An `auth: true` endpoint gets `dependencies=[Depends(require_auth)]`,
  a clean analogue of `r.With(middleware.RequireAuth)`.
- **FastAPI accepts chi's `{id}` path syntax unchanged**, so manifest paths need
  no translation.

### 6.2 `Envelope[T]` is a real generic model

Go hand-built the envelope and had no way to type it. Here it is a generic
Pydantic model, so `response_model=Envelope[list[Project]]` enforces the
contract *and* keeps the generated OpenAPI honest. This is strictly better than
the source.

### 6.3 Pydantic collapses three Go mechanisms into one

`{{.Name}}Request` structs, the hand-written 422 mapping, and
`jsonValidationError`'s `fields` map become one generated Pydantic model plus a
`RequestValidationError` handler that reshapes Pydantic's error list into the
contract's `fields` dict. Also strictly better.

### 6.4 The envelope must be defended harder than in Go

FastAPI returns `{"detail": ...}` by default — for `HTTPException`, for
`RequestValidationError`, for 404 and for 405. **Four exception handlers** are
required to produce `{ok, data, meta, error, code, fields}`, versus Go's single
`NotFoundHandler`/`MethodNotAllowedHandler` pair. This is the easiest thing in
the whole port to get subtly wrong, and it is contract-visible, so it gets
dedicated tests asserting the envelope on: an unmatched `/api/` path, a wrong
method, a malformed body, a raised `HTTPException`, and an unhandled exception.

### 6.5 Auth becomes a dependency, not middleware

Go's `Auth(store)` middleware runs on every request, static files included, and
hits the database for the session epoch each time. A `Depends` runs only where
asked. `require_auth` and `require_page_auth` build on one
`current_user_id(request) -> int | None` provider. Deliberate improvement.

`require_page_auth` raises `HTTPException(303, headers={"Location": "/login"})`
— a browser navigation must not be answered with an error envelope (GOVA
DECISIONS §7 survives intact).

### 6.6 `models.Time` becomes an annotated Pydantic type

The reason is unchanged: Pydantic v2 serializes `datetime` with microseconds,
and Swift's `.iso8601` strategy rejects fractional seconds. The mechanism:

```python
Timestamp = Annotated[
    datetime,
    BeforeValidator(parse_sqlite_datetime),                       # ← Go's Scan
    PlainSerializer(lambda d: d.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    return_type=str),                             # ← Go's MarshalJSON
]
```

`parse_sqlite_datetime` accepts the same layout set as Go's `scanLayouts`.
Python's `sqlite3` datetime adapters are deprecated in 3.12+ and are **not**
registered — the column is read as a string and parsed here, which is what the
Go version effectively did too.

### 6.7 The connection pool is hand-rolled

`database/sql` gave Go pooling for free. Here `Database` holds one write
connection behind a `threading.Lock` (SQLite serializes writes anyway) and a
`threading.local()` read connection per worker thread — FastAPI runs sync
handlers in a threadpool, so `check_same_thread=False` plus per-thread
connections is the correct shape. PRAGMAs are executed on connect
(`journal_mode=WAL`, `busy_timeout=5000`, `foreign_keys=ON`,
`synchronous=NORMAL`); Python's `sqlite3` URI support does not carry them the
way the Go driver's DSN did. `isolation_level=None` for autocommit.

### 6.8 Reserved model names expand

GOVA reserves `time`, `user`, `mobile_token`. Python must also refuse anything
that would shadow a module in `models/` or a stdlib name imported there —
minimally `timestamp`, `query`, `cache`, `db`, `types`, `json`, `datetime`. A
model named `query` would silently shadow `models/query.py` and break every
generated `GetPage`.

### 6.9 Everything else ports 1:1

`fcntl.flock` for the workspace lock (LOCK_EX / LOCK_SH, held across the whole
transaction), `os.replace` for atomic writes, the manifest
upsert/canonicalize/sha256 logic, the sort/filter column whitelist, the two
rate-limit buckets with their asymmetric budgets, the epoch-revocation session
cookie, `clientIP`'s trusted-peer resolution, the CSRF safe-method allowlist and
cookie-presence rule, the CSP string. **GOVA DECISIONS §§1, 3–9, 11 survive
verbatim.** §2 changes mechanism only. §10 gets *more* important.

## 7. The builder CLI

Identical surface to `gova`, so the skills' command tables port as a rename:

```
bb inspect
bb sql      -query "..."
bb model    -name x -fields ...
bb page     -file x -title X -path /x [-auth]
bb handler  -name x -method POST -path /api/v1/... [-auth] [-summary ...]
            [-request-schema ...] [-response-schema ...]
bb resource -name x -fields ...
bb version | help
```

Identical field DSL: `name:type` where type ∈ `string int float boolean
timestamp`; `name:ref:<model>` for a foreign key; `name:email|date|datetime|time|json`
for a string with a format hint. Identical refusals: credential columns,
reserved names, `/api/` for pages, non-`/api/v1/` for handlers, missing `id` or
`created_at`, unknown field types, declared/actual column type mismatch.

`argparse` subparsers replace Go's `flag.NewFlagSet`, with the same
single-dash flag spelling (`-name`, not `--name`) so every command in the docs
and skills is copy-pasteable across both templates.

### Templates

| Template | Emits |
|---|---|
| `model.py.j2` | Pydantic row model + `<Name>Request` + `<Name>Model` data-access class |
| `test_model.py.j2` | round-trip tests against a temp database |
| `resource_handlers.py.j2` | five typed CRUD functions |
| `test_resource_handlers.py.j2` | `TestClient` tests per endpoint |
| `handler.py.j2` | one custom endpoint stub |
| `page.html.j2` / `page.js.j2` | inert shell + ES module |
| `list_page.html.j2` / `list_page.js.j2` | list + create form + delete buttons |
| `routes_gen.py.j2` | `register_generated(app, db, cache)` |
| `pages_gen.py.j2` / `test_pages_gen.py.j2` | `register_pages(app)` + per-page reachability assertions |

The JS templates are near-verbatim copies from gova-monolith — same endpoints,
same envelope, same `api.js`.

## 8. Bootstrap ordering

A circularity worth naming before it bites: `src/app` ships with `api.json`
pre-populated (the `user` model, eight auth endpoints, two pages) **and** with
`routes_gen.py` / `pages_gen.py` / `test_pages_gen.py`, which are generator
output. But the generator does not exist until Phase 2.

Resolution: hand-write all four files in Phase 1, then in Phase 3 assert the
builder reproduces them **byte-identically** from the committed `api.json`. That
turns the circularity into a test — the strongest available check that the
generator and the committed baseline agree.

## 9. Risks

| Risk | Mitigation |
|---|---|
| No compile gate | ruff + mypy strict + pytest + the scratch-render test (§4) |
| Jinja2 whitespace in generated Python | render loosely, `ruff format` after; non-fatal on failure |
| Dependency surface larger than Go's | runtime deps capped at four; a new one needs a justification line in the plan, and `/security:analyze` audits it |
| Developer reflex to reach for `Jinja2Templates` | explicit Critical Constraint; grep for it in `/build` step 7 |
| `async def` creeping in and blocking the event loop | explicit Critical Constraint; ruff rule where one exists |
| Silent divergence from the frozen contract | contract tests on the envelope and timestamp format; a test asserting `api.json` and the live route table agree |

## 10. Critical Constraints (for CLAUDE.md)

1. **No raw SQL outside `models/`.** Parameterized queries only — never an
   f-string, `%`, or `.format()` into SQL. Column names reach SQL only through
   the generated allow-list.
2. **No HTML rendering in Python. No `Jinja2Templates` anywhere in `src/app`.**
   Jinja belongs to the builder. Handlers return the envelope; pages are inert
   HTML served by `FileResponse`. *(This is the first thing a FastAPI developer
   reaches for, and it would quietly dissolve the frontend boundary.)*
3. **Everything annotated. `mypy --strict` is the gate.** No `Any`, no bare
   `except`.
4. **Handlers are sync `def`.** `async def` only where it does no blocking I/O.
5. **JS rules, verbatim from GOVA:** never `innerHTML` with user data; never
   `eval`/`exec`; always fetch through `api.js`; never log a token, password or
   session value.
6. **No Node, no npm, no CDN.** Tailwind standalone. The CSP blocks CDNs —
   including Swagger's, which is why its assets are vendored.
7. **Security is already wired.** Protect an endpoint or page with `auth: true`
   in `api.json`; never re-check auth inside a handler.
8. **A new dependency requires a justification line in the plan.**

## 11. Implementation phases

Ordered by dependency. Tasks within a phase are file-disjoint and batchable.

**Phase 0 — Skeleton and gate**
`Dockerfile`, `docker-compose.yml`, `entrypoint.sh`, `bb` wrapper, `pyproject.toml`
for both packages, ruff/mypy/pytest config, `scripts/verify`, `env.example`,
`.gitignore`. Exit: `docker compose up` serves a stub `/api/v1/_version` and
`verify` passes.

**Phase 1 — App infrastructure** *(the largest port; all hand-written)*
`db/database.py` + `schema.sql` + `testutil.py`; `cache/cache.py`;
`models/timestamp.py`, `query.py`, `user.py`, `mobile_token.py`;
`handlers/envelope.py` (+ the four exception handlers), `paging.py`,
`clientip.py`, `ratelimit.py`, `auth.py`, `auth_mobile.py`, `register.py`,
`version.py`, `home.py`; `middleware/security.py`, `csrf.py`, `auth.py`;
`static/js/lib/{api,auth}.js`, `static/pages/{home,login,register}.html` and
their modules; `static/css/input.css`; vendored Swagger assets; hand-written
`api.json`, `routes_gen.py`, `pages_gen.py`, `test_pages_gen.py` (§8);
`main.py`. Exit: registration, login, logout, logout-all, bearer login and the
`me` endpoints all work and are tested; the envelope tests pass.

**Phase 2 — Builder core**
`lock.py`, `manifest.py`, `fields.py`, `schema.py`, `render.py`, `inspect.py`,
`tools.py`, `cli.py` and their tests, including the concurrency test (four
simultaneous `bb resource` runs lose nothing). Exit: `./bb inspect` reads the
committed manifest.

**Phase 3 — Templates**
All templates from §7, the per-command end-to-end CLI tests, the
`test_render_resource_to_dir` scratch-render test, and the §8 byte-identity
assertion. Exit: `./bb resource` on a fresh table produces working, tested,
type-clean CRUD.

**Phase 4 — Harness**
`CLAUDE.md` + `AGENTS.md` symlink, `docs/API-CONTRACT.md` (frozen copy),
`docs/DECISIONS.md` (§6), `README.md`, `SEED.md`, `CHECKLIST.md`, the three
`bb-*` skills, `/build`, `/launch`, `/security:analyze`, `.opencode/` agents and
symlinks, three install scripts.

**Phase 5 — End-to-end validation**
Build a throwaway two-resource app with the finished template; run
`/security:analyze`; point `gova-ios`'s `/export:mobile` at
`black-box/src/app/api.json` and confirm it exports with **zero changes to that
repo**. That last check is the proof that decision #3 was honored.

## 12. Out of scope

- Changing anything on `docs/API-CONTRACT.md`. It is frozen; a change there is a
  change to two templates and one shipped iOS client.
- Migrations. GOVA has none (`bb sql` is the whole story); black-box adds none.
- Multi-worker deployment, Postgres, Redis, background jobs.
- A Python port of `gova-ios`. It reads `api.json` off disk and needs nothing.
