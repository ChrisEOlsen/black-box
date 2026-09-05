# black-box Port — Implementation Plan

> **For agentic workers:** steps use checkbox (`- [ ]`) syntax for tracking.
> Design source: [`docs/specs/2026-09-04-black-box-design.md`](../specs/2026-09-04-black-box-design.md).

**Goal:** Build the black-box template — a Python/FastAPI + vanilla JS
equivalent of gova-monolith, wire-compatible with it and with `gova-ios`.

**Architecture:** Two containers (`app`, `builder`) over one SQLite file.
`src/app/api.json` is the source of truth for the served surface; the `bb` CLI
renders real `.py`/`.html`/`.js` files from Jinja2 templates and regenerates
`routes_gen.py` / `pages_gen.py` from the manifest. Auth ships as committed
code. `ruff check` + `ruff format --check` + `mypy --strict` + `pytest`
replaces Go's compile gate.

**Tech Stack:** Python 3.13, FastAPI, stdlib `sqlite3` (WAL), Pydantic v2,
vanilla ES modules, Tailwind standalone, uv.

## Global Constraints

- The wire contract is **frozen**: `docs/API-CONTRACT.md` is copied from
  gova-monolith and must not be edited. Envelope shape, error-code set,
  timestamp format, pagination rules, auth endpoints and the `api.json` schema
  are all fixed.
- `src/app/api.json` keeps that exact path — `gova-ios` reads it off disk.
- The eight Critical Constraints in spec §10 bind every file under `src/app`.
- Runtime dependencies are capped at four: `fastapi`, `uvicorn`, `pydantic`,
  `bcrypt`. A fifth needs a written justification.
- Pinned versions: fastapi 0.141.1, uvicorn 0.52.4, pydantic 2.13.5,
  bcrypt 5.0.0, pytest 9.1.1, httpx 0.28.1, ruff 0.16.6, mypy 2.3.1.
- Every phase ends green on `scripts/verify` (or its local equivalent while the
  Docker daemon is unavailable — see Deviations).

## Deviations from the spec

1. **No `uv.lock`.** Exact `==` pins in `requirements.txt` + `requirements-dev.txt`,
   installed with `uv pip install --system`. A lockfile would need `uv` on the
   host or a bootstrap build; pins give the same reproducibility with none of
   the ceremony. Spec §3 said "uv + lockfile"; this is the same guarantee.
2. **Local venv during the build.** The Docker daemon is down on this machine,
   so phases are verified against a local `.venv` via `scripts/verify --local`.
   `scripts/verify` (container path) is written but stays unverified until the
   daemon is up; Phase 5 gates on running it for real.

---

## Phase 0 — Skeleton and gate

**Files:** `Dockerfile`, `docker-compose.yml`, `entrypoint.sh`, `bb`,
`env.example`, `.gitignore`, `scripts/verify`, `src/app/{pyproject.toml,
requirements.txt,requirements-dev.txt,main.py}`, `src/builder/{pyproject.toml,
requirements.txt}`, `data/.gitkeep`, `logs/.gitkeep`.

**Produces:** the `bb` wrapper contract (`docker compose exec -T builder bb "$@"`),
the verify contract (restart → wait on `/api/v1/_version` → ruff → ruff format
→ mypy → pytest), tool config for ruff/mypy/pytest.

- [ ] Dockerfile with `base` → `app` / `builder-cli` targets; Tailwind standalone
      binary by arch; builder templates baked in at image build time.
- [ ] Compose file: `app` (ports, `./src:/src`, `./data:/data`, `./logs:/logs`)
      and `builder` (`restart: unless-stopped`).
- [ ] `main.py` stub serving `GET /api/v1/_version` →
      `{"api_version":"1.0.0","min_client_version":"1.0.0"}` in the envelope.
- [ ] ruff (`select` incl. `E,F,I,UP,B,S,ASYNC`), mypy `strict = true`, pytest
      config in `src/app/pyproject.toml`.
- [ ] `scripts/verify` with a `--local` mode that skips Docker.

**Exit:** `verify --local` green; `/api/v1/_version` answers in the envelope.

---

## Phase 1 — App infrastructure (all hand-written)

Largest phase. Straight port of committed Go. Tasks are file-disjoint after 1.1.

- [ ] **1.1 Data layer** — `db/database.py` (1 write conn behind a `Lock`,
      thread-local read conns, PRAGMAs on connect, `schema.sql` applied at open),
      `db/schema.sql` (users, mobile_tokens, login_attempts), `db/testutil.py`
      (`open_test(extra_schema)` → temp file, never `/data/app.db`).
      **Produces:** `Database`, `open_test`.
- [ ] **1.2 Timestamp + query helpers** — `models/timestamp.py` (`Timestamp`
      annotated type: `BeforeValidator` parsing the SQLite layout set,
      `PlainSerializer` emitting `%Y-%m-%dT%H:%M:%SZ`), `models/query.py`
      (`QueryOpts`, `order_by_clause`, `filter_field`, `InvalidQuery`).
      **Produces:** `Timestamp`, `QueryOpts`, `InvalidQuery`.
- [ ] **1.3 Envelope + exception handlers** — `handlers/envelope.py`:
      `Envelope[T]`, `Meta`, the closed code set, `code_for_status`,
      `normalize_data`, and the four handlers (HTTPException,
      RequestValidationError, 404, 500). **Produces:** `Envelope`, `json_ok`,
      `json_list`, `json_error`, `json_validation_error`.
- [ ] **1.4 Cache** — `cache/cache.py`: `get`/`set`/`bust(prefix)`, TTL, lock,
      background janitor. **Produces:** `Cache`.
- [ ] **1.5 Paging + client IP** — `handlers/paging.py` (`query_int` clamping,
      limit 1–200 default 50), `handlers/clientip.py` (trusted-peer set,
      `CF-Connecting-IP`, right-most non-trusted `X-Forwarded-For`).
- [ ] **1.6 Security + CSRF middleware** — `middleware/security.py` (the CSP
      string verbatim from GOVA), `middleware/csrf.py` (safe-method allowlist,
      verify only when the session cookie is present, `compare_digest`).
- [ ] **1.7 Session + auth guards** — `middleware/auth.py`: HMAC-signed cookie
      carrying `{uid, epo, exp}`, `set_session`, `clear_session`,
      `current_user_id`, `require_auth`, `require_page_auth` (303 to `/login`).
      **Produces:** `require_auth`, `require_page_auth`, `CurrentUser`.
- [ ] **1.8 User + token models** — `models/user.py` (bcrypt, `session_epoch`,
      `bump_session_epoch`), `models/mobile_token.py` (64-hex token, SHA-256
      stored). **Produces:** `UserModel`, `MobileTokenModel`.
- [ ] **1.9 Rate limiter** — `handlers/ratelimit.py`: two buckets (5/15min per
      address, 20 per account), account key = SHA-256 of the lowercased email,
      `clear_attempts` on success, windowed.
- [ ] **1.10 Auth endpoints** — `handlers/auth.py`, `auth_mobile.py`,
      `register.py`: the eight endpoints from the contract, exact payloads.
- [ ] **1.11 Static frontend** — `static/js/lib/{api,auth}.js` (verbatim from
      GOVA), `static/css/input.css`, `static/pages/{home,login,register}.html`
      + their modules, vendored Swagger assets under `static/vendor/swagger/`.
- [ ] **1.12 Baseline manifest + generated files** — hand-write `api.json`
      (user model, 8 auth endpoints, 2 pages), `handlers/routes_gen.py`,
      `handlers/pages_gen.py`, `handlers/test_pages_gen.py`. Phase 3 asserts the
      builder reproduces these byte-identically (spec §8).
- [ ] **1.13 Wire it up** — `main.py`: exception handlers, middleware,
      `/static` mount, `GET /`, `register_pages`, `_version`,
      `register_generated`, local-only `/docs`.

**Exit:** register → login → me → logout → logout_all → bearer login → me_token
→ logout_token all work and are tested; envelope tests cover all four failure
paths; `verify --local` green.

---

## Phase 2 — Builder core

- [ ] **2.1 `lock.py`** — `fcntl.flock` LOCK_EX/LOCK_SH held across the whole
      transaction; `atomic_write` via temp file + `os.replace`; `BB_LOCK_PATH`
      override for tests.
- [ ] **2.2 `fields.py`** — the `name:type` DSL, `ref:`/format hints, known
      types, `is_safe_ident`, `to_pascal`, `to_plural`, py/sql/input type maps.
- [ ] **2.3 `schema.py`** — `PRAGMA table_info` introspection, normalized SQL
      types, `require_implicit_columns` (id, created_at), credential-column
      refusal, expanded reserved-name list (spec §6.8).
- [ ] **2.4 `manifest.py`** — the dataclasses, upsert with conflict detection,
      canonicalize, sha256 hash, `routes_gen`/`pages_gen` regeneration.
- [ ] **2.5 `render.py`** — Jinja2 env (`trim_blocks`, `lstrip_blocks`),
      filters mirroring GOVA's `funcMap`, `ruff format` subprocess pass that is
      non-fatal on failure.
- [ ] **2.6 `inspect.py` + `tools.py` + `cli.py`** — the seven commands with
      single-dash flags, path namespace validation, self-registration.
- [ ] **2.7 Tests** — per-module plus the four-way concurrency test proving no
      registration is lost.

**Exit:** `bb inspect` reads the committed manifest; builder suite green.

---

## Phase 3 — Templates

- [ ] **3.1** `model.py.j2` + `test_model.py.j2`
- [ ] **3.2** `resource_handlers.py.j2` + `test_resource_handlers.py.j2`
- [ ] **3.3** `handler.py.j2`, `page.{html,js}.j2`, `list_page.{html,js}.j2`
- [ ] **3.4** `routes_gen.py.j2`, `pages_gen.py.j2`, `test_pages_gen.py.j2`
- [ ] **3.5** `test_render_resource_to_dir` — render into a scratch copy of
      `src/app`, then run ruff + mypy + pytest over it. **Highest-value test in
      the repo** (spec §4).
- [ ] **3.6** Byte-identity assertion: builder regeneration of the Phase 1.12
      baseline `routes_gen.py` / `pages_gen.py` matches the committed files.
- [ ] **3.7** End-to-end CLI tests, one per command.

**Exit:** `bb resource` on a fresh table yields working, tested, type-clean CRUD.

---

## Phase 4 — Harness

- [ ] **4.1** `CLAUDE.md` (+ `AGENTS.md` symlink) — the rules, the command
      table, the constraint list from spec §10, the harness section.
- [ ] **4.2** `docs/API-CONTRACT.md` (frozen copy, header noting it is shared)
      and `docs/DECISIONS.md` (spec §6, written as standalone entries).
- [ ] **4.3** `README.md`, `SEED.md`, `CHECKLIST.md`.
- [ ] **4.4** Skills: `bb-brainstorm`, `bb-writing-plans`, `bb-build-execution`
      (+ its four scripts and two prompt templates).
- [ ] **4.5** Commands: `/build`, `/launch`, `/security:analyze`.
- [ ] **4.6** `.opencode/` agents + command symlinks; three install scripts.

**Exit:** a fresh clone can be installed under either harness.

---

## Phase 5 — End-to-end validation

- [ ] **5.1** Bring the Docker daemon up; run the real `scripts/verify`.
- [ ] **5.2** Build a throwaway two-resource app with the finished template.
- [ ] **5.3** Run `/security:analyze` over `src/app`; fix Critical/High/Medium.
- [ ] **5.4** Point `gova-ios`'s `/export:mobile` at
      `black-box/src/app/api.json` and confirm it exports with **zero changes to
      that repo**. This is the proof that the frozen-contract decision held.
