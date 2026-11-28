---
name: writing-alembic-migrations
description: Writes, tests and dry-runs an Alembic migration for the GDG Backend's MySQL database. Use when changing app/DB/schema.py, adding a table or column, moving or backfilling data, adding a file under alembic/versions, or when a deploy failed in its "alembic upgrade head" step or with multiple heads.
---

# Writing an Alembic migration

The deploy runs `alembic upgrade head` on staging when a PR merges into `dev`, and on production
when `dev` reaches `main`. Nothing else ever migrates those databases. A migration is "done" only
when it would pass that step on real data, not just on the empty test database.

Copy this checklist and tick it off:

```
- [ ] 1. Revision created by hand on the current head
- [ ] 2. upgrade() and downgrade() written; schema.py matches
- [ ] 3. Data checks run before any DDL
- [ ] 4. Tests pass: test_migrations.py plus a test for this migration
- [ ] 5. Dry run on a local copy of staging passes
- [ ] 6. PR says which revision it adds and what the dry run printed
```

## 1. Create the revision

```bash
uv run alembic heads                      # must print exactly one head
uv run alembic revision -m "add x to y"   # empty file, needs no database
```

Never `--autogenerate`, and so never `uv run poe revision` (it autogenerates). It can't write the data
moves, and it guesses MySQL types and collations. Keep `include_object` in `alembic/env.py`: it stops
Alembic from treating the database views in `VIEWS` as tables.

## 2. Write it

- Change `app/DB/schema.py` in the same PR. `tests/test_migrations.py` runs `alembic check` and fails
  when the models and the migrations disagree.
- New tables get UUID ids: `mysql.CHAR(36, charset="ascii", collation="ascii_bin")` here,
  `UUID_CHAR` with `default=new_id` in the model. A foreign key needs the same type and collation on
  both sides, or MySQL refuses it (error 3780).
- `ON DELETE CASCADE` in the migration means `passive_deletes=True` on the model's relationship.
- Write a real `downgrade()`. The test suite runs a downgrade drill on the newest revisions.

## 3. Check the data before any DDL

MySQL commits every DDL statement on its own, so a migration that fails halfway leaves a half-changed
schema and the deploy can't simply retry. Put every check that can fail first and raise before the
first `op.create_table` or `op.alter_column`. `_check_fake_event_dependencies()` in
`alembic/versions/a7b8c9d0e1f2_separate_bonuses_from_events.py` is the model to follow.

Production data has shapes the tests never made. The bonuses migration failed on staging because of
email history, a reused pipeline request, and events saved through `POST /events` with
`location_type='none'`. Before you write a data migration, list every table with a foreign key to the
tables you touch, and decide what happens to their rows.

## 4. Test

```bash
uv run pytest -o addopts="" -q tests/test_migrations.py      # one head, models match, downgrade drill
```

A migration that moves data needs its own test: seed the old shape at the previous revision, upgrade,
assert, downgrade. `tests/test_bonuses_migration.py` does exactly that, using the testcontainer's root
URL from `tests.conftest._drill_server_root` to get a scratch schema.

## 5. Dry run on a copy of staging

This copies staging into your local `scores-local` MySQL container with a read-only `mysqldump`, then
runs the same upgrade the deploy will run. It replaces all local data, and prefixes every event name in
the copy with `[Dev] `. Always pass `--source`: the script defaults to copying production.

```bash
uv run python scripts/run_local_DB.py --source staging --yes
LOCAL=mysql+pymysql://root:rootpassword@127.0.0.1:3306/scores-local
DATABASE_URL=$LOCAL uv run alembic current        # staging's revision
DATABASE_URL=$LOCAL uv run alembic upgrade head   # what the deploy will do
DATABASE_URL=$LOCAL uv run alembic current --check-heads
```

If it fails, fix the migration and run all of section 5 again: the failed run left the copy
half-migrated. For a migration that moves data, ask Albrrak773 before also dry-running on a copy of
production (`--source prod`). Staging is an older copy and lacks newer rows, which is how events 514-517
got past staging.

Never point `alembic upgrade` at the Infisical `staging` or `prod` env.

## Two heads

When two PRs each add a migration on the same parent, `alembic heads` prints two revisions and the
deploy stops. Add a merge revision on your branch, then run section 4 again:

```bash
uv run alembic merge -m "merge <a> and <b>" <rev_a> <rev_b>
```

The downgrade drill in `tests/test_migrations.py` pins the last merge's two parents and their common
ancestor (`2f8c7b6a901d`, `b3e7c1d9a5f2` -> `194e7997ddc9`). Update those three revisions to your
merge's parents and their common ancestor, or the drill fails.
