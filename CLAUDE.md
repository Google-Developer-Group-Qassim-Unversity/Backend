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

> This file is intentionally short and covers only what is easy to get wrong.
> Run `/init` if you want a fuller codebase overview generated.
