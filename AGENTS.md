# Backend

The one backend for GDG Qassim's score system: the API behind both the admin
panel and the public site. FastAPI + SQLAlchemy + Alembic on MySQL.

## Three repositories

The system is split across three repos in the
`Google-Developer-Group-Qassim-Unversity` org, meant to be cloned side by side:

| Repo | What | Deployed at |
|------|------|-------------|
| [`Backend`](https://github.com/Google-Developer-Group-Qassim-Unversity/Backend) | FastAPI + SQLAlchemy + Alembic on MySQL; the one API every frontend calls | `refactor.albrrak773.com` (pm2 `GDG-backend`) |
| [`Admin`](https://github.com/Google-Developer-Group-Qassim-Unversity/Admin) | Next.js admin panel, Clerk auth | `admin.gdg-q.com` |
| [`Website`](https://github.com/Google-Developer-Group-Qassim-Unversity/Website) | Next.js public site: leaderboard, events, member profiles, wallet passes | `gdg-q.com` |

`Backend` and `Admin` were one repo, `score-leaderboard-Admin-app` (archived;
its issues and PRs #1-#173 are still there). `Website` was
`score-leaderboard-app`. A path written as `Admin/lib/access.ts` or
`Backend/app/helpers.py` means that file in the sibling repo.

A change that spans repos is one branch per repo with the same name - the PR
checks pick up the other repo's matching branch (falling back to `dev`) - and
the backend merges first, since the frontend expects its API to exist.

## When something is broken in production

**Read [docs/LOGGING.md](docs/LOGGING.md) first.** It covers
where the logs are, how to trace a single request end to end, and when to use
Sentry instead of SSH.

The short version: the backend runs under PM2 as `GDG-backend` on a VPS, logs to
stdout, and every line carries the worker pid and a request id:

```
INFO [pid:1996265] [req:6cefc82ee87c] app.middleware: GET /health -> 200 in 3.9ms
```

SSH host, user and key live in Infisical under `prod` / `/VPS` - the runbook has
a copy-pasteable helper that loads them without writing anything to disk, and
handles the fact that `pm2` is not on the default non-interactive PATH.

Every response carries that id in the `X-Request-ID` header, and it is set as a
Sentry tag, so a Sentry issue links straight to its log lines.

## Branches: everything goes through `dev`

**Never push to `main`, and never open a PR into `main`.** Branch from `dev`,
open the PR into `dev`. A merge to `dev` deploys staging
(`admin-dev.gdg-q.com`, against `scores_staging`, a copy of prod); `main` only
ever receives `dev`: once in a while, one large `dev` -> `main` PR, merged with
a merge commit, which deploys prod. Don't open that PR unless asked - its
timing is the team's call. PR checks run on PRs into `dev` only.

Staging and local dev redirect every email away from real members. See
[docs/STAGING.md](docs/STAGING.md).

## Conventions

These are enforced by tests, not just preference:

- **Database sessions** come from the `session: DB` dependency
  (`app/dependencies.py`). Never construct a session in a handler. Code outside
  the request cycle - background tasks, scripts - uses `db_session()` from
  `app/DB/main.py`.
- **Errors** are raised, not translated. `app/error_handlers.py` owns the
  mapping to HTTP responses; handlers should not wrap themselves in
  `try/except` to produce a 500.
- **Auth** is a dependency, not a parameter, and it comes from our database,
  never from Clerk metadata. Admin routes take a permission guard from
  `app/services/permissions/guards.py`: `Staff`, `Require(Perm.X)`,
  `Require(Perm.X, event_departments)` when the permission is checked against
  the department of the thing in the path, or `SuperAdmin`. Use
  `member: CurrentMember` when the handler needs the caller, and `CurrentAccess`
  when it checks a permission itself (e.g. a department from the body).
  `tests/test_route_auth.py` pins the permission on every route and fails if
  one changes. [docs/PERMISSIONS.md](docs/PERMISSIONS.md)
  explains the model and how to add a permission.
- **Every route declares a `response_model`.** `response_model` silently drops
  undeclared keys, and the leaderboard app (a separate repository) consumes
  several of these endpoints, so `tests/test_response_models.py` checks each
  model against the keys its handler actually returns.
- **Logging** is stdlib `logging` with a module-scoped
  `logger = logging.getLogger(__name__)`. `print()`, `write_log` and `LogFile`
  are banned and the test suite fails if they reappear.
- **Ids are UUIDs.** Every new table's id is a UUID:
  `id: Mapped[str] = mapped_column(UUID_CHAR, primary_key=True, default=new_id)`.
  `new_id` (`app/DB/ids.py`) makes time-ordered UUIDv7s, so ordering by id still
  means creation order, as it did with integers. Every column pointing at it is
  `UUID_CHAR` too (in a migration,
  `mysql.CHAR(36, charset="ascii", collation="ascii_bin")`; foreign keys need
  both sides to match). In the API and the frontend these ids are strings. Never
  an auto-increment integer. The older tables that still have integer ids
  (members, events, departments, logs and the rest) stay that way: too much
  depends on them, including the leaderboard app. `tests/test_uuid_ids.py`
  lists them and fails on any other table with an integer id.

## Action IDs are hardcoded

`app/DB/logs.py` and `app/routers/action.py` reference production `actions`
primary keys as literal lists. They decide which events can be created and
which can be attended, they have already drifted apart, and they are wrong in
every database that is not production - which is why the attendance tests
patch the lookup out. Read
[docs/HARDCODED_ACTION_IDS.md](docs/HARDCODED_ACTION_IDS.md)
before changing anything in that area; it has the fix plan and a checklist.

## Linear: use `linear-gdg`, never `linear`

This project's tickets live in the GDG Linear workspace, reached through an MCP
server named `linear-gdg` (`https://mcp.linear.app/mcp`). It is declared in this
repo for every agent we use:

| Agent | File | Sign in once with |
|-------|------|-------------------|
| Claude Code | `.mcp.json` | `/mcp` |
| Codex | `.codex/config.toml` (project must be trusted) | `codex mcp login linear-gdg` |
| opencode | `opencode.json` | `opencode mcp auth linear-gdg` |
| Cursor | `.cursor/mcp.json` | Settings → MCP |
| VS Code (Copilot) | `.vscode/mcp.json` | the server's "Start" prompt |
| Gemini CLI | `.gemini/settings.json` | `/mcp auth linear-gdg` |

Sign in with the Linear account that is a member of the GDG workspace. No token
is committed. A `linear` server, if one is loaded, is some other workspace
configured outside this repo: never read or write GDG tickets through it.

## Commands

```bash
uv run pytest                 # needs docker (testcontainers) or DATABASE_URL
uv run ruff check --fix .
uv run ruff format .
uv run pyright .
uv run pytest tests/routers/test_events.py
uv run pytest -k test_name
uv run alembic upgrade head     # local DB only
uv run alembic revision -m "desc"   # write migrations by hand
uv run poe dev                # runs via infisical, port 7001
```

Club Structure has an opt-in browser/API/MySQL check that drives the real admin
page. With the `Admin` repo cloned next to this one (or `ADMIN_DIR` pointing at
it), its dependencies and Playwright Chromium installed, run
`RUN_CLUB_BROWSER=1 uv run pytest` with Docker available and `DATABASE_URL`
unset. See `Admin/tests/club-structure/README.md` for setup and scope.

CI runs `ruff format --check` as its own job, separate from `ruff check`. Run
`uv run ruff format .` before every commit - `ruff check --fix` does not
rewrite formatting (line wraps, collapsing short dicts/calls onto one line), so
a change that only shortens or lengthens a string can flip the format check
even when `ruff check` is clean.

The conventions above came out of a FastAPI-idioms refactor; see
[docs/FASTAPI_REFACTOR.md](docs/FASTAPI_REFACTOR.md) for what changed, the
numbers, and what was deliberately left alone.

FastAPI backend for a score/leaderboard admin system. Python 3.13, managed with **uv**.

## Verification

After changes, always run in this order:

```bash
uv run ruff format .        # format first
uv run ruff check --fix .   # lint fix
uv run pytest               # tests
```

Use `uv run pyright .` for typechecking, as configured by `poe typecheck`.
Also run `uv run ruff check .`. Fix new lint and type errors introduced by the change.

## Setup

- **Python 3.13** required (`.python-version`)
- `uv sync --dev` to install all dependencies
- Secrets managed via **Infisical**. If not installed, ask the user to install it first:
  - **Windows**: `winget install infisical`
  - **macOS/Linux**: `brew install infisical/get-cli/infisical`
  - **Arch**: `yay -S infisical-bin`
  - **Ubuntu**: `curl -1sLf 'https://artifacts-cli.infisical.com/setup.deb.sh' | sudo -E bash && sudo apt-get update && sudo apt-get install -y infisical`
- Then ask the user to log in by following the steps in `docs/ENVIRONMENT_VARIABLES.md`
- Once logged in, prefixing any command with `infisical run --path=/admin-backend --env=dev --` will inject secrets (e.g. `run.sh`)
- `.env.local` is for optional local overrides only — it takes priority over Infisical values
- FastAPI entrypoint: `app.main:app` → Swagger docs at `http://localhost:7001/docs`

## Architecture

- **`app/main.py`** – FastAPI app, CORS, router registration, static file mount for uploads
- **`app/config.py`** – all env vars accessed through `config` singleton; `ENV=testing` skips dotenv loading
- **`app/semesters.py`** – semester resolution. Semesters live in the `semesters` DB table (admin-editable via `/semesters`), *not* in config — never reintroduce a hardcoded semester dict, and never use the current semester as a route default (it must be resolved per request)
- **`app/DB/schema.py`** – SQLAlchemy ORM models (source of truth for DB schema). Alembic uses `Base.metadata` from here
- **`app/DB/main.py`** – `SessionLocal` and `engine` creation
- **`app/routers/`** – API route handlers, one file per domain (events, members, points, etc.)
- **`app/helpers.py`** – shared utilities and the Clerk guards (`authenticated_guard`, `optional_clerk_guard`)
- **`app/services/permissions/`** – who can do what: the `Perm` catalogue, `resolve_access`, and the route guards (`Staff`, `Require`, `SuperAdmin`)
- **`alembic/env.py`** – migration env; excludes DB views listed in `VIEWS` set via `include_object`
- **Source of truth for DB**: `app/DB/schema.py` + existing migration files
- **Do not use `--autogenerate`** for new migrations. Create empty revisions with `alembic revision -m "desc"` and write them manually
- For FK relationships with `ON DELETE CASCADE`, always add `passive_deletes=True` to the SQLAlchemy relationship (see schema docstring)
- Alembic ignores the views in `alembic/env.py` `VIEWS` set — do not remove `include_object`

## Testing

- Uses **testcontainers** (MySQL 8.0) by default; set `DATABASE_URL` to use an existing DB (CI mode)
- Alembic migrations run automatically in `conftest.py` before any tests
- Auth bypass fixture chain: `client` (no auth) < `clerk_client` (member) < `admin_client` / `super_admin_client`
- Each test gets transaction rollback via connection-level binding to `SessionLocal`
- **Never import app modules at module level in conftest** — env vars must be set first (delayed imports inside fixtures)
- Factories in `tests/factories.py`; assertion helpers in `tests/utils.py`
- `--cov=app` is on by default

## Ruff Config

- Line length: 120
- `ruff.lint.select = ["FAST", "TID252"]` (FastAPI-specific + absolute imports)
- `ruff.format.skip-magic-trailing-comma = true`
- Unused imports in `__init__.py` are allowed (`F401` suppressed)

## CI (`.github/workflows/`)

This repo is the backend only. The admin frontend lives in the `Admin` repo and
the public site in `Website`. See “Three repositories” above.

- **pr.yml** – on PRs into `dev`
  - `lint` – `uv run ruff check .` and `uv run ruff format --check .`
  - `test` – `RUN_CLUB_BROWSER=1 uv run pytest` (the fixtures also run `alembic upgrade head` on a fresh MySQL, and `tests/test_migrations.py` fails on multiple heads or models that no migration matches). The Club Structure browser check needs the admin frontend, so the job also checks out `Admin` - the branch with the PR's name if there is one, else `dev`.
- **deploy.yml** – on push to `dev` (staging, port 7502) or `main` (production, port 7501); SSH-deploys the backend under PM2 with the matching Infisical env. Run it manually to redeploy.
  The deploy builds the release in `~/GDG-backend.new` (or `-staging.new`), runs `alembic upgrade head` against that environment's database, replaces the live release, restarts, and fails unless `/health/db` and `/events/` answer. Migrations are never run by hand.
