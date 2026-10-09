# Staging

A second, full copy of the admin app on the same VPS as production, running the
`dev` branch against a copy of the production database. Use it to click
through a change on real data before it reaches `main`.

|                    | Production                 | Staging                        |
|--------------------|----------------------------|--------------------------------|
| Branch             | `main`                     | `dev`                          |
| Admin site         | `admin.gdg-q.com`          | `admin-dev.gdg-q.com`          |
| Backend API        | `refactor.albrrak773.com`  | `api-dev.gdg-q.com`            |
| Database           | `scores`                   | `scores_staging`               |
| Infisical env      | `prod`                     | `staging`                      |
| Backend (pm2)      | `GDG-backend`, port 7501   | `GDG-backend-staging`, port 7502, 1 worker |
| Frontend (docker)  | `gdg-admin-web`, port 3012 | `gdg-admin-web-staging`, port 3013 |
| Sentry environment | `production`               | `staging`                      |

Local `poe dev` uses the Infisical `dev` env, whose `DATABASE_URL` is a MySQL
on your own machine (`127.0.0.1/scores-local`), so a migration you are still
writing never touches staging. Alembic migrates whatever `DATABASE_URL` points
at; `ALEMBIC_DATABASE_URL` is set in every env but nothing reads it.

## Deploying

Merge or push to `dev`. `.github/workflows/deploy.yml` deploys whichever half
changed, exactly as it does for `main`: it migrates `scores_staging` to the
release's head first, so a broken migration fails the deploy and leaves the
running staging app alone. Run the workflow by hand on `dev` to redeploy both
halves.

Getting a change to production stays the same: a PR into `main`. `dev` is not
merged into `main`; it is reset to `main` whenever it drifts too far
(`git push --force origin main:dev`, after checking nobody is mid-test).

## Emails never reach real members

`scores_staging` is a copy of prod, so it holds every member's real address,
and the pipeline sweep sends email on its own. With `ENV=Staging` every send is
redirected (`app/services/email_redirect.py`):

- to **the member who clicked**, so whoever sends a blast or certificates gets
  every copy, opening with a box that lists who it was really for;
- to **`EMAIL_REDIRECT_FALLBACK`** (comma-separated, in Infisical `staging`)
  when nobody clicked, such as the sweep. If it is empty, those sends fail
  rather than go out as addressed.

Local dev (`ENV=development`) redirects the same way.

## What staging still shares with production

Same Clerk instance (the copied members' Clerk ids only make sense there), same
Google account for Forms, same Cloudflare R2 bucket, same wallet signing
identity, same `send-certificates` service. An upload from staging lands in the
production bucket, and a Google Form created from staging is a real form on the
club's account.

## Refreshing the database

`scores_staging` drifts from prod as people test. To start over, drop its
tables and copy prod again (`START TRANSACTION WITH CONSISTENT SNAPSHOT`, so
prod is never locked). Two traps from the first copy, on 9 Oct 2026:

- MySQL view definitions name their schema (`` `scores`.`members` ``), so a
  plain dump leaves staging's views reading prod. Rewrite them to
  `scores_staging`.
- Skip only `VIRTUAL GENERATED` / `STORED GENERATED` columns. `extra LIKE
  '%GENERATED%'` also matches `DEFAULT_GENERATED`, which is every
  `DEFAULT CURRENT_TIMESTAMP` column.

The app user `scores` needs `GRANT ALL ON scores_staging.*`; that grant is run
as the server's `admin` user.
