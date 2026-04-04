# AGENTS.md

FastAPI backend for a score/leaderboard admin system. Python 3.13, managed with **uv**.

## After Any Changes

Always run in this order:

```bash
uv run ruff format .        # format first
uv run ruff check --fix .   # lint fix
uv run pytest               # tests
```

Also run `uv run mypy .` and `uv run ruff check .` — these have **many pre-existing errors that are OK to ignore**. Only investigate errors on lines you added or modified.

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

## Commands

```bash
uv run pytest                                  # all tests
uv run pytest tests/routers/test_events.py     # single file
uv run pytest -k test_name                     # single test
uv run ruff format .                           # format
uv run ruff check --fix .                      # lint
uv run mypy .                                  # typecheck (pre-existing errors expected)
uv run alembic upgrade head                    # apply migrations
uv run alembic revision -m "desc"              # new empty migration (write manually)
```

## Architecture

- **`app/main.py`** – FastAPI app, CORS, router registration, static file mount for uploads
- **`app/config.py`** – all env vars accessed through `config` singleton; `ENV=testing` skips dotenv loading
- **`app/DB/schema.py`** – SQLAlchemy ORM models (source of truth for DB schema). Alembic uses `Base.metadata` from here
- **`app/DB/main.py`** – `SessionLocal` and `engine` creation
- **`app/routers/`** – API route handlers, one file per domain (events, members, points, etc.)
- **`app/helpers.py`** – shared utilities and auth guards (`authenticated_guard`, `admin_guard`, `super_admin_guard`)
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

All trigger on push/PR to `main` with `Backend/**` path filter:

- **backend-test.yml** – `uv run pytest`
- **backend-ruff-format.yml** – `uv run ruff format --check .`
- **backend-ruff-autofix.yml** – `uv run ruff check --fix-only --diff .`
- **backend-migration.yml** – MySQL 8 service container, `alembic upgrade head`
- **deploy-backend.yml** – SSH deploy on push to main (PM2, infisical prod env, port 7501)
