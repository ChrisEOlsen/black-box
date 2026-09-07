# Agent Context: black-box

You are the Lead Architect of a black-box app: Python + FastAPI, SQLite,
vanilla ES modules, Tailwind. Run `/build` to build from `SEED.md`, `/launch`
to deploy.

> Claude Code reads this as `CLAUDE.md`; opencode reads `AGENTS.md`, a symlink
> to it. One copy, no drift. See **Harnesses** at the end.

## How this works

**You do not write this application by hand. You drive a generator.** The
`./bb` CLI renders deterministic Python, HTML and JS from templates. Decide
*what* to build, run the right command, then customize what it produced.
Generated code arrives already wired, already tested, and already obeying the
rules below.

**Two containers, one database.** `app` runs the FastAPI server — restart it to
pick up edits and recompile CSS. `builder` holds the `bb` CLI and its
templates, kept separate so `docker compose restart app` cannot kill a scaffold
mid-write. SQLite lives at `/data/app.db`. `./bb` is a thin wrapper that execs
into the builder container; run it from anywhere in the repo.

> The `builder` image installs its templates at **image build** time. After
> editing anything under `src/builder/`, run `docker compose up -d --build
> builder` — a plain restart reruns the old code and silently generates
> old-shape files.

**`src/app/api.json` is the source of truth for the served surface.** Models,
endpoints and pages live there. The tools write it and regenerate
`handlers/routes_gen.py` and `handlers/pages_gen.py` from it; `main.py` mounts
both with one call each. **Never hand-wire a route and never edit a `*_gen.py`
file** — if a route is wrong, the manifest is wrong.

**Auth ships with the template.** Sessions, CSRF, bcrypt, rate limiting, and
bearer tokens for native clients are committed code in `src/app`, not something
you scaffold. See `docs/API-CONTRACT.md`.

**Where things go.** Handlers return the envelope and live in `handlers/`.
Database access is model methods only, in `models/`. Page shells are inert HTML
in `static/pages/`. All DOM rendering is vanilla ES modules in `static/js/`.

**The loop for any feature:** create the table → call a scaffold command →
customize what it generated → restart. Start with `./bb inspect`.

**Before claiming anything works:** run `scripts/verify`. Not one of its four
checks — all of them, which is what the script is for.

---

## The Golden Recipe

### 1. Table first
Always `id INTEGER PRIMARY KEY` and `created_at DATETIME DEFAULT
CURRENT_TIMESTAMP` — both are required and checked at scaffold time.

```sql
CREATE TABLE projects (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    status     TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### 2. Scaffold

```bash
./bb sql -query "CREATE TABLE projects (...)"
./bb resource -name project -fields name:string,status:string
```

That one command writes the model, five CRUD handlers, a page with a create
form and delete buttons, and tests for all of it, then registers everything.
For anything that is not a CRUD resource, use `./bb model`, `./bb page` and
`./bb handler`.

### 3. Customize
Edit the generated `.js` for behavior and `.html` for layout. Python logic
stays in `handlers/`, data access in `models/`.

### 4. Restart
`docker compose restart app` reloads the app and recompiles Tailwind.

---

## Mandatory Scaffolding Rule

**For a feature handler or a JS page, run the `bb` command FIRST — before
writing any code.** The sequence is always: **command → generated file →
customize it.**

Never write a feature handler from scratch and then run the command. Never skip
`./bb resource` because "it's simpler to just write it".

**Infrastructure is hand-written** — `middleware/`, `db/`, `cache/`, `deps.py`,
`handlers/envelope.py`, `handlers/auth*.py`, `handlers/register.py`,
`handlers/ratelimit.py`, `handlers/clientip.py`, `handlers/paging.py`,
`models/user.py`, `models/mobile_token.py`, `models/timestamp.py`,
`models/query.py`, `static/js/lib/*.js`. These are app-wide plumbing created
once, not per-feature. If you hand-write something, say which rule made it
infrastructure.

---

## Critical Constraints

1. **No raw SQL outside `models/`.** Model methods only —
   `model.get_page(...)`, never `db.query(...)` from a handler. Parameterized
   queries only: never an f-string, `%`, or `.format()` carrying a value into
   SQL. Column names reach SQL only through a model's generated allow-list.

2. **No HTML rendering in Python. No `Jinja2Templates` anywhere in `src/app`.**
   Jinja belongs to the builder. Handlers return the envelope; pages are inert
   HTML served by `FileResponse`. *(This is the first thing a FastAPI developer
   reaches for, and it would quietly dissolve the whole frontend boundary.)*

3. **Everything is annotated, and `mypy --strict` is the gate.** No `Any`
   crossing a module boundary, no bare `except`. Python has no compiler; this
   is what replaces it — see `docs/DECISIONS.md` § 1.

4. **Handlers are sync `def`.** FastAPI runs them in a threadpool. Use
   `async def` only for something that does no blocking I/O; an `async` handler
   that touches SQLite stalls the event loop for every other request.

5. **JS safety:**
   - NEVER `element.innerHTML = userValue` — use `textContent`, or
     `createElement` + `setAttribute` for structure.
   - NEVER `eval()` or `new Function()` with external data.
   - ALWAYS fetch through `api.js` — never a raw `fetch()`.
   - NEVER `console.log()` a token, password, or session value.

6. **No Node, no npm, no CDN.** Tailwind standalone only. The CSP blocks
   outside origins — including Swagger's, which is why its assets are vendored
   under `static/vendor/`.

7. **Security is already wired.** CSRF, sessions, rate limiting, bcrypt and the
   CSP live in `middleware/` and `handlers/`. Scaffolds are guarded by default;
   `-public` opts one out, and `auth` in `api.json` is the record of that
   choice. Do not re-check auth inside a handler — the generated route does it.

8. **A new dependency needs a written justification.** Runtime dependencies are
   capped at four (`fastapi`, `uvicorn`, `pydantic`, `bcrypt`). Dependency
   surface is this stack's specific risk and `/security:analyze` has to audit
   it, so a fifth is a decision, not a convenience.

Full wire details — envelope, codes, timestamps, pagination, the manifest
fields native clients read — are in **`docs/API-CONTRACT.md`**, which is
**frozen and shared with gova-monolith**. Read it before changing anything a
client can see. **`docs/DECISIONS.md`** explains why the non-obvious pieces are
shaped the way they are.

---

## Commands

Run `./bb help`, or `./bb <command> -h` for a command's flags.

| Command | Use | Tests? |
|---|---|---|
| `./bb inspect` | **First.** Manifest, files on disk, divergence | — |
| `./bb sql -query "..."` | Create tables — always before a model | — |
| `./bb model -name x -fields ...` | Data layer only; registers the model, no route | Yes |
| `./bb handler -name x -method POST -path /api/v1/...` | One custom JSON endpoint; self-registers | No — write one |
| `./bb page -file x -title X -path /x` | `.html` + `.js` at a human URL; no handler needed | Yes |
| `./bb resource -name x -fields ...` | Full CRUD + page + form. Table must exist | Yes |

**`page`, `handler` and `resource` require a signed-in user by default.** Pass
`-public` to open one up, and mean it: scaffolded CRUD includes create, update
and delete, so a public resource is world-writable data. The default is the
thing that ships, so the default is the safe one.

`bb page` refuses `/api/`; `bb handler` requires `/api/v1/`. The two namespaces
cannot collide. Resource pages are plural (`/projects`); the auth pages are
singular (`/login`, `/register`).

**Fields** are a comma-separated `name:type` list —
`-fields "title:string,quantity:int,due_at:timestamp"`.

**Types:** `string`, `int`, `float`, `boolean`, `timestamp`. An unknown type is
an error. A DATETIME column must be `timestamp`, never `string`.
`name:ref:<model>` is a foreign key; `name:email|date|datetime|time|json` is a
string with a format hint that drives the form control on web and iOS.

**Credential columns are refused.** The tools generate generic CRUD, where a
request that omits a field overwrites it. Authentication is hand-written.

**Reserved names are refused** — the template's own modules and anything in the
standard library, because a model named `json` would shadow it for every
generated import. See `docs/DECISIONS.md` § 12.

---

## Testing

- Scaffold commands generate tests alongside the code they emit.
- **Hand-customized logic gets its own test.** Tests live beside their code as
  `test_<name>.py`, so every file belongs to exactly one feature. Use
  `db.testutil.open_test(extra_schema)` for anything touching the database — a
  temp file, never `/data/app.db`.
- `handlers/test_pages_gen.py` is regenerated with `pages_gen.py` and asserts
  every registered page serves its shell. Never hand-edit it.
- **No JS test runner** — Constraint 6 rules out Node. Client code is verified
  in the browser.
- Verify with **`scripts/verify`**, which restarts the app and then runs
  `ruff check`, `ruff format --check`, `mypy` and `pytest`. Pass `--local` to
  run the same checks against `./.venv` without Docker.

**Your editor is not the gate.** Pylance and basedpyright apply rules mypy does
not. `pyrightconfig.json` in each package pins them to `strict` and silences the
pytest false positives, so a fresh clone is quiet — but pyright ships through
npm, which Constraint 6 rules out, so it stays advice and `scripts/verify` stays
the standard. Both packages and a freshly scaffolded app are clean under it
today; keep them that way where it costs nothing, and never add a
`# type: ignore` to satisfy a rule the gate does not run.

**If you change a template under `src/builder/`, the gate is
`src/builder/test_render_resource_to_dir.py`.** It renders a resource into a
scratch copy of `src/app` and runs all four checks over the result. Nothing
else proves a template emits working Python.

---

## How a build runs

**Subagents author code; the orchestrator touches everything shared.** Up to 3
implementers customize their own scaffold output at once, and none of them runs
`bb`, restarts the app, runs the suite, or commits. Those are the
orchestrator's, one batch at a time.

That split is what makes parallelism safe without any locking. Two tasks with
disjoint file lists still share the `app` container, the test suite, and the git
index — so nobody but the orchestrator touches them. See
`bb-build-execution` § The model.

**The builder serializes itself regardless.** Every mutating `bb` command holds
an flock on `/src/.bb-lock` across its whole read→write→regenerate transaction
(`src/builder/bb/lock.py`), and every file lands via temp-file + rename. That is
what keeps `api.json` correct when two harness sessions are open at once.
Verified: four concurrent `bb resource` processes register all four resources
with nothing lost.

**No worktrees.** The `builder` container's bind mounts point at one absolute
path, fixed at `docker compose up`. A worktree lives elsewhere, so `./bb` would
write to the wrong checkout. Use a plain feature branch in the main checkout
(`git checkout -b build/<app-name>`).

---

## Harnesses

This project runs under **Claude Code** and **opencode**, with the same
workflow, because everything defining it lives in files both read:

| What | Where | How each finds it |
|---|---|---|
| These rules | `CLAUDE.md` | Claude Code directly; opencode via the `AGENTS.md` symlink |
| `/build`, `/launch`, security audit | `.claude/commands/` | Claude Code reads the dir; `.opencode/command` symlinks into it |
| The three `bb-*` skills | `.claude/skills/` | Both — opencode scans `.claude/skills/**/SKILL.md` |
| The `bb` builder | `./bb` in the repo root | Both — it is a CLI, not an MCP server |

Install with `./install-claude.sh`, `./install-opencode.sh`, or both.
**Nothing above is duplicated per harness. Do not fork it.**

Four differences:

1. **Batched questions** — `AskUserQuestion` (Claude Code) / `question`
   (opencode). Both take several questions per call.
2. **Subagent model** — Claude Code takes `model` per dispatch; opencode's
   `task` tool does not, so the tiers live in `.opencode/agent/*.md`. Under
   opencode, dispatch `bb-implementer`, `bb-reviewer`, or `bb-architect`.
3. **Final review** — the `code-review` skill (Claude Code), or `/review` /
   `bb-architect` (opencode).
4. **Command names** — the security audit is `/security:analyze` in Claude Code
   and `/security-analyze` in opencode.

opencode loads its config once at startup. After editing `opencode.json`,
anything under `.opencode/`, a skill, or a command, quit and reopen it.
