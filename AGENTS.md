# Backend

FastAPI + SQLAlchemy + Alembic on MySQL, Python 3.13 with uv. Every GDG Qassim client calls this one
API: `Admin` (admin.gdg-q.com), `Website` (gdg-q.com) and the iOS app (`App`, shipped to TestFlight).
The repos sit side by side; `Admin/lib/api.ts` means that file in the sibling repo.

## Branches, deploys, environments

- Branch from `dev`, open PRs into `dev`. Never push to `main` or open a PR into it unless asked:
  `main` only receives `dev` (the `releasing-to-production` skill).
- A merge to `dev` deploys **staging** (`api-dev.gdg-q.com`, database `scores_staging`, Infisical
  `staging`); a merge to `main` deploys **production** (`refactor.albrrak773.com`, database `scores`,
  Infisical `prod`). `docs/STAGING.md` has the full table.
- The deploy runs `alembic upgrade head` on that environment's database before it swaps the release,
  so a failed migration stops the deploy and the old release keeps serving. After the swap there is no
  fallback: if the new release fails its smoke test, that environment is down until a fix deploys.
  Nobody runs migrations against staging or prod by hand.
- Local `poe dev` uses Infisical `dev`, whose database is a local MySQL (`scores-local`), never staging.
- Staging and local dev redirect every email away from real members (`app/services/email_redirect.py`).
  Staging still shares production's R2 bucket, Google account and wallet identity: an upload or a
  Google Form made from staging is real.
- A change that spans repos uses the same branch name in each, and this repo merges and deploys first.
  Backend's and Admin's PR checks check out each other's same-named branch (else `dev`); Website's
  checks never run against Backend.

## Commands

Secrets come from Infisical (`--path=/admin-backend`); the `poe` tasks already wrap `infisical run`.

```bash
uv sync --dev                                   # a fresh worktree has no .venv
uv run poe dev                                  # API on :7001, docs at /docs
uv run ruff format . && uv run ruff check --fix .   # CI checks both, separately
uv run pyright <changed files>                  # ~44 errors already exist in untouched code; CI doesn't run it
uv run pytest -o addopts="" -q tests/routers/test_events.py   # one file, no coverage, ~40 s
uv run pytest -o addopts="" -q -k name          # one test
```

- Every pytest run starts a MySQL testcontainer (Docker must be running) and applies all migrations
  first, so even one test takes ~35 s. The full suite takes about 6 minutes: run it in the background,
  never in a foreground command with a short timeout. CI runs it as
  `RUN_CLUB_BROWSER=1 uv run pytest`, which also drives the Admin repo's Club Structure page.
- `ruff check --fix` does not reformat. Run `ruff format` before every commit or the format check fails.

## Conventions the tests enforce

- **Sessions** come from the `session: DB` dependency. Code outside a request (background tasks,
  scripts) uses `db_session()` from `app/DB/main.py`.
- **Errors are raised**, not caught into a 500 in the handler; `app/error_handlers.py` maps them.
- **Auth** is a dependency and comes from our database, never Clerk metadata. Admin routes take a guard
  from `app/services/permissions/guards.py` (`Staff`, `Require(Perm.X)`, `SuperAdmin`);
  `tests/test_route_auth.py` pins every route's permission. See `docs/PERMISSIONS.md`.
- **Every route declares a `response_model`.** It silently drops undeclared keys, so
  `tests/test_response_models.py` checks each model against what its handler returns.
- **Logging** is `logger = logging.getLogger(__name__)`; `print()`, `write_log` and `LogFile` fail the suite.
- **New tables use UUIDv7 string ids**: `mapped_column(UUID_CHAR, primary_key=True, default=new_id)`, and
  `mysql.CHAR(36, charset="ascii", collation="ascii_bin")` in migrations. The old integer-id tables stay
  integer; `tests/test_uuid_ids.py` lists them.
- **Semesters** live in the `semesters` table and are resolved per request (`app/semesters.py`). Never
  hardcode one or default a route to the current semester.

## Changing the API: shipped iOS builds can't update

TestFlight builds call production and stay installed for weeks after a new build ships, so API changes
are additive: add fields and routes, keep the old ones and their types until Albrrak773 says the old
builds are gone. The app sends `X-App-Version` and `X-App-Build` on every request (nothing here reads
them yet). Its expectations are in `App/docs/API-CONTRACT.md` on App's `main` branch (a local `gdg-app`
checkout of `dev` is an old scaffold). `docs/points-model.md` shows the pattern: compatibility shims
(`/custom/*`, the `events` list on `/points/*`) kept until a later cleanup phase.

## Migrations

Use the `writing-alembic-migrations` skill for any schema or data migration. In short: write them
by hand, never `--autogenerate`, keep one head, and
dry-run a data migration against a local copy of staging before merging.

## Gotchas

- `app/DB/logs.py` and `app/routers/action.py` hardcode production `actions` ids. They are wrong in every
  other database. Read `docs/HARDCODED_ACTION_IDS.md` before touching events, attendance or actions.
- The Website caches public data (30 s on its server, 60 s in the browser). After committing a change to
  points, events, members or semesters, call one of the best-effort helpers in `app/leaderboard_cache.py`
  (`refresh_attendance_points_cache()` for points, as `attendance.py` and `bonuses.py` do;
  `reset_leaderboard_cache_best_effort()` for everything else). They log failures instead of raising, so
  a Website outage never fails an admin's save. The bare `reset_leaderboard_cache()` raises.
- A production bug: read `docs/LOGGING.md` first. Every response has an `X-Request-ID`, which is also a
  Sentry tag (Sentry MCP `sentry-gdg`) and appears on every log line.
- Every env var is declared on `Settings` in `app/config.py` and listed in `docs/ENVIRONMENT_VARIABLES.md`;
  add both together.

## Linear

GDG tickets (`GDG-54`) live in the GDG Linear workspace: the `linear-gdg` MCP server from `.mcp.json`, or
`linear-gdgq` in Albrrak773's own setup. Never read or write them through another Linear server.
