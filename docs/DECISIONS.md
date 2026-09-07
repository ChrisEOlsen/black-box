# Decisions

Why a few things are shaped the way they are. Source comments point here rather
than carrying the argument inline.

This template is a port of [`gova-monolith`](../../gova-monolith) (Go · chi ·
SQLite · vanilla JS). Entries 1–3 and 8–14 are where the Python version differs
from it; the rest are inherited arguments that survive the port unchanged,
restated here so this repo stands alone.

---

## 1. `ruff` + `mypy` + `pytest` replaces the compiler

gova-monolith's whole safety model rests on one fact: `go test ./...` compiles
the entire package. That is what makes it safe for three subagents to edit
different files without building, what catches a bad template render, and what
its `TestRenderResourceToDir` exists to exercise.

Python has no equivalent. A generator that emits broken Python fails at the
first request, in production, silently.

So `scripts/verify` runs four checks, in this order:

| Go | here | catches |
|---|---|---|
| `go build ./...` | `ruff check` | undefined names, bad signatures, unused imports |
| type errors | `mypy --strict` | type errors across module boundaries |
| `go test ./...` | `pytest` | behavior |
| `gofmt` after render | `ruff format --check` | layout of generated code |

`mypy --strict` is reachable *because* generated code is fully annotated, which
is itself an argument for keeping full-parity codegen rather than dynamic
dispatch: a runtime-generic CRUD router would be untypeable and unreviewable.

`src/builder/test_render_resource_to_dir.py` is the load-bearing test. It
renders a resource into a scratch copy of `src/app`, then runs all four checks
over the result and imports the app. On its first run it caught four defects no
unit test would have seen. **If you change a template, that test is the gate.**

## 2. Generated Python is formatted after rendering, not aligned by hand

Python is whitespace-sensitive, so aligning a Jinja2 `{% for %}` against output
of unknown length is the largest authoring hazard in this generator. Templates
render loosely; `bb.render.format_python` fixes the layout afterwards.

Two details are deliberate:

- **Only import *order* is auto-fixed** (`ruff check --select I --fix`). An
  *unused* import is left alone so it fails `ruff check` and sends the author
  back to the template's `{% if %}`. gova-monolith gets this discipline free
  from the Go compiler, which refuses to build an unused import; here it has to
  be arranged.
- **A formatting failure is not fatal.** The unformatted bytes are written and
  the real error surfaces at `ruff check` pointing at the actual problem, rather
  than leaving the author an empty file and a message about formatting.

The full destination path is handed to ruff as the stdin filename, which is how
ruff finds `src/app/pyproject.toml` and therefore the app's own line length and
`known-first-party` list. With a bare basename it resolves config from the
builder's directory and sorts `handlers`/`models` as third-party.

## 3. Handlers are typed functions, not factories

gova-monolith's handlers are factories: `ProjectListGET(database, appCache)`
returns a closure. Ported literally that would be a disaster, because FastAPI
reads the **signature** of the endpoint function to derive path parameters,
query parameters, body validation and OpenAPI. A closure taking `Request`
discards all of it.

So a generated handler is a plain module-level function:

```python
def project_create(body: ProjectRequest, db: DatabaseDep, cache: CacheDep) -> Envelope[Project]:
```

Three consequences:

- **`deps` in the manifest is vestigial.** FastAPI's DI does what gova's
  `callExpr` did by hand. The field is still written so a manifest from either
  template parses the same, but nothing reads it.
- **`routes_gen.py` is simpler than `routes_gen.go`.** One `add_api_route` per
  endpoint; `auth: true` becomes `dependencies=[Depends(require_auth)]`, a clean
  analogue of `r.With(middleware.RequireAuth)`.
- **The `id` path parameter is named `id`**, not `item_id`, because the manifest
  path is `/api/v1/projects/{id}` and FastAPI binds by name. The path shape is
  fixed by the frozen contract, so the parameter name follows it.

## 4. Timestamps are RFC3339 with no fractional seconds

Pydantic v2 serializes a `datetime` with microseconds and a `+00:00` offset.
Swift's `.iso8601` decoding strategy **rejects fractional seconds**, so a
default-serialized timestamp parses fine in JavaScript and fails on iOS — a wire
defect only the second client ever finds.

`models.timestamp.Timestamp` pins the format: a `BeforeValidator` that accepts
every layout SQLite can hand back, and a `PlainSerializer` that emits
`%Y-%m-%dT%H:%M:%SZ`. A model field holding a moment is declared `Timestamp`,
never a bare `datetime`, and a `DATETIME` column is declared `timestamp`, never
`string`.

`timestamp_to_db` exists because Python's `sqlite3` datetime adapters are
deprecated since 3.12 and deliberately not registered: binding a `datetime`
directly raises. Generated models bind every timestamp column through it, which
also guarantees the stored text is the same RFC3339 the wire uses.

## 5. Two rate-limit buckets, with different budgets

`clear_attempts` deletes by key and a successful login calls it, so **the key is
the isolation**. Two consequences:

- Each login endpoint gets its own **address** bucket. Sharing one lets a
  success on the bearer login erase the cookie login's failures.
- A second bucket meters the **account being attacked**. Without it, an attacker
  clears their address bucket by logging into an account they own between
  guesses — four wrong guesses, one real login, repeat, forever, with the
  limiter reporting itself healthy because the fifth attempt never arrives.

The budgets differ deliberately (5 per address, 20 per account). A per-account
bucket is also a per-account *lockout*: at threshold N, anyone who knows an
address can deny that user their login for fifteen minutes. Set equal to the
address budget that is cheap denial of service; set far above it, a lone
attacker exhausts their own address budget long before reaching it.

The account key is a SHA-256 digest of the lowercased address — the table is
written by unauthenticated callers, so a raw key would make it a log of every
address anybody probed. Lowercasing is load-bearing: without it, varying the
case bypasses the control entirely.

The window makes the limit a *rate*. A counter that only increments turns the
budget into a lifetime quota, so the bucket re-locks on its next attempt
forever — which behind a shared NAT is every user on that address.

## 6. `client_ip` needs a trusted-peer notion

Two failures that point in opposite directions, which is why fixing one does not
reveal the other:

- **Trusting a forwarding header from any peer** lets a direct caller name
  itself and mint a fresh bucket per request — unlimited guessing, limiter
  green.
- **Ignoring forwarding headers entirely** puts every caller behind a reverse
  proxy in one bucket, so five bad logins lock the whole deployment.

One trusted-peer set (`TRUSTED_PROXIES`) resolves both: an untrusted peer names
only itself; a trusted proxy's `CF-Connecting-IP`, or the **right-most**
`X-Forwarded-For` entry that is not itself a trusted hop, is believed.
Right-most because a client can prepend anything, so everything left of the
first hop we trust is attacker-authored.

The default is empty, which is the safe direction: an app with no proxy in front
of it must not believe a header any client can set.

## 7. CSRF verifies when a cookie is present

The scheme protects a browser riding an ambient credential. A caller with **no
cookies at all** — a native client, a webhook — has nothing for a forged
cross-site request to replay, and forcing the scheme on it only breaks it. A
bearer request is exempt for the same reason, and `/api/v1/auth/login_token` is
exempt by path because it is the request that *issues* a token and cannot carry
one yet.

Safe methods are **allowlisted**, not unsafe ones denylisted: a
`POST || PUT || DELETE` check silently does not run on PATCH. Inverted, a method
nobody thought of is verified by default.

A request carrying a session cookie but no CSRF cookie fails closed: the token
compared against is a fresh random the client cannot know.

## 8. Auth guards are dependencies, not middleware

gova-monolith runs `Auth(store)` as middleware on every request — static files
included — and hits the database for the session epoch each time. A FastAPI
`Depends` runs only where it is asked for, so `require_auth` costs nothing on
the routes that do not use it.

`require_page_auth` raises a 303 to `/login` rather than returning a JSON 401,
because a browser navigation must not be answered with an error envelope. It is
a **courtesy, not a boundary**: the page shell is inert and every datum on it
comes from an `/api/v1/` endpoint, so those are what must carry `auth: true`.
What the page guard buys is removing the flash of a page the visitor is about to
be bounced off.

## 9. The four exception handlers

FastAPI answers `{"detail": ...}` by default — for a raised `HTTPException`, for
a request-validation failure, for an unmatched path, and for a wrong method. All
four are contract-visible, and all four are re-shaped in
`handlers/envelope.py`.

This is more work than gova needs (it overrides two chi fallbacks) and it is the
easiest thing in the port to get subtly wrong, so each path has a dedicated test
in `handlers/test_envelope.py`. Two details:

- The envelope drops unset members in a `model_serializer` rather than through
  per-route `response_model_exclude_none`. A route that forgot the flag would
  ship `"error": null` on every success — a contract break no test on that route
  would catch.
- Under `/api/` the answer is an envelope; elsewhere it is the browser's
  ordinary error page. Same judgement `require_auth` and `require_page_auth`
  make.

## 10. Swagger is vendored, and local-only

The CSP is `script-src 'self'`, and FastAPI's `/docs` loads Swagger UI from
jsdelivr. Rather than widen the policy, the two asset files are committed under
`static/vendor/swagger/` and `/docs` is mounted only when
`APP_ENV != production` — a deployed app has no business publishing its full API
surface, and a local build gets a genuinely useful debugging view.

`openapi.json` is suppressed in production for the same reason.

## 11. The workspace lock wraps the whole transaction

Builds are serial — one task at a time — so the lock is not there to make
subagents safe. It is there because two harness sessions can be open at once
(Claude Code and opencode share one checkout and one pair of containers), and
because it costs nothing.

Without a lock held across the full read→upsert→write→regenerate cycle, two
scaffolds read the same base manifest, both write, and the second silently
erases the first's registration — a lost update no same-key conflict check can
see, because each writer's snapshot was already stale.

An `fcntl.flock` on `/src/.bb-lock` covers it: `LOCK_EX` for mutators, `LOCK_SH`
for readers. Files land via temp-file + `os.replace` so a reader never sees a
truncated `.py`. Verified by `test_lock.py`, which runs four concurrent
`bb resource` processes and asserts every registration and every file survives.

## 12. The reserved-name list is longer than Go's

gova reserves `time`, `user` and `mobile_token`. Python needs more.

`models/` is a plain package directory on `sys.path`, so a model named `json`
would produce `models/json.py` and shadow the standard library for every
generated import inside that package. Go's equivalent collision is a compile
error naming the file; Python's is an `ImportError` far from the cause, or worse,
a silently different module.

So `bb` refuses the template's own module names (`timestamp`, `query`, `user`,
`mobile_token`, `cache`, `db`, `database`, `main`, `conftest`) **and** anything
in `sys.stdlib_module_names`.

## 13. Pages and endpoints are separate manifest tables

A page has no method beyond GET, no request or response body, and no deps — it
is not part of the API surface a native client consumes. Keeping the tables
apart also keeps the namespaces disjoint: `bb page` refuses `/api/`, `bb handler`
requires `/api/v1/`, so neither can shadow the other.

Resource pages are always plural (`/projects`) and the auth pages take their
singular verb (`/login`, `/register`). Because `to_plural` never returns its
input unchanged, a resource named `login` lands at `/logins` — the namespaces
cannot collide without a reserved-word list.

## 14. The committed generated files are byte-checked

`src/app` ships with `api.json`, `routes_gen.py`, `pages_gen.py` and
`test_pages_gen.py` already written, because the app has to run before the
builder exists. That is a bootstrap circularity.

`src/builder/test_baseline_identity.py` closes it: the committed files must be
byte-identical to a fresh render from the committed manifest, and the manifest's
recorded hash must match its contents. Without that, the generator and the
baseline can drift, and the next scaffold silently rewrites files nobody
reviewed.

## 15. Auth is committed code, not a scaffold

Sessions, CSRF, bcrypt, rate limiting and bearer tokens are ordinary Python in
`src/app`, covered by the same gate as everything else. The scaffolding commands
simply refuse credential columns (`bb/schema.py`), because generic CRUD is a
shape where a request that merely omits a field overwrites it — so a credential
column reachable from one is a credential anybody can blank.

The manifest still *describes* auth: `api.json` ships pre-populated with the
user model, the eight auth endpoints and the two pages, and `routes_gen.py` is
generated from it like any other route. Nothing is special-cased.

## 16. A list's `data` is never `null`

A typed client decoding an array fails on `null`. Generated models return `[]`,
and `response_model=Envelope[list[T]]` makes the guarantee structural — Pydantic
rejects `None` where a list is declared. `normalize_data` in
`handlers/envelope.py` is the second guard for hand-written handlers.

## 17. The builder is a CLI, not an MCP server

It was an MCP server in an earlier version of gova-monolith. For six commands
taking string arguments the protocol was pure overhead, and it cost real
coverage: driving the command layer required a JSON-RPC client, so nothing
tested it. As a CLI those are ordinary functions with an overridable
`Workspace`, and `test_cli.py` drives all six end to end.

One process per invocation also means a single `flock` covers every caller,
instead of an in-process lock *and* a cross-process one.

What was given up: MCP put the tool schemas in the model's context
automatically. That is now a table in `CLAUDE.md`, backed by `bb help`. It would
be worth reconsidering if the builder ever needed to run on a different machine
from the agent — that is the case MCP is actually for.

## 18. One uvicorn worker, and no `--reload`

The in-process cache and the single-writer SQLite pool both assume one process,
exactly as the Go binary did. Multiple workers would give each its own cache,
so a write in one would not bust the others'.

`--reload` is omitted deliberately: a reloader watching a tree an agent is
mid-write in produces crash loops rather than convenience, and the explicit
`docker compose restart app` keeps `scripts/verify` a single, honest gate.

## 19. The containers are not root, and the app image keeps its dev tools

Two container decisions that look inconsistent and are not.

**Non-root.** Neither image runs as root. `USER` is numeric rather than a
created account, so the build cannot fail over a uid that already exists in the
base image — Python needs no `passwd` entry.

The complication is bind mounts. The app compiles CSS into `./src` and owns
`./data` and `./logs`; the builder writes generated files into `./src`. On
Linux a container uid that does not match the host owner turns every one of
those into a permission error, and the symptom — `bb resource` failing
mid-scaffold — points nowhere near the cause. So `docker-compose.yml` sets
`user:` from `APP_UID`/`APP_GID`, and the install scripts record the host's
real `id -u` / `id -g` in `.env`. The image default of 1000 is only what you
get running it without compose.

**Tailwind is pinned and checksum-verified.** It was fetched from `latest` at
image build, so every downstream app's build depended on whatever shipped that
morning — not reproducible, and an unverified binary pulled over the network
into every image. A pin fixes reproducibility; the `sha256sum -c` is what makes
the fetch trustworthy. Updating means bumping the version *and* both digests,
which is the point: it is a deliberate act.

**The dev tools stay in the app image**, and that is not an oversight.
`scripts/verify` runs `ruff`, `mypy` and `pytest` **inside the app container** —
that is the whole gate, and Python has no compiler behind it (§ 1). Stripping
them would mean the artifact that gets verified is not the artifact that runs,
which trades a real guarantee for a smaller image.

The honest cost is a larger production image containing a test runner. If that
matters for a particular deployment, the fix is a separate `prod` build target
installing only `requirements.txt` — not removing the tools the gate needs.
