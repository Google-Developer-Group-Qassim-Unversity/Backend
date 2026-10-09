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
| Clerk instance     | production (`clerk.gdg-q.com`) | development (`quality-ram-46`), shared with local dev |
| Sign-in            | Clerk's email code         | developer email + `8888`       |

Local `poe dev` uses the Infisical `dev` env, whose `DATABASE_URL` is a MySQL
on your own machine (`127.0.0.1/scores-local`), so a migration you are still
writing never touches staging. Alembic migrates whatever `DATABASE_URL` points
at; `ALEMBIC_DATABASE_URL` is set in every env but nothing reads it.

## Deploying

Every change goes through `dev`; nothing is pushed or PR'd into `main` directly.

1. Branch from `dev`, open the PR into `dev`. PR checks run there.
2. Merging it deploys staging. `.github/workflows/deploy.yml` deploys whichever
   half changed and migrates `scores_staging` to the release's head first, so a
   broken migration fails the deploy and leaves the running staging app alone.
3. Once staging looks right, open a `dev` -> `main` PR and merge it with a merge
   commit. That push to `main` deploys production the same way.

Run the workflow by hand on either branch to redeploy both halves.

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

## Signing in

Staging's sign-in page asks for an email and the fixed code `8888` instead of
running Clerk's emailed code (`Frontend/lib/staging-sign-in.ts`). It accepts only
the emails in `STAGING_LOGIN_EMAILS` (comma-separated, Infisical `staging` /
`/admin-frontend`): staging holds a copy of every member's real data, so add a
developer there before they can sign in. It holds the Development department's
members (six on 9 Oct 2026, Fall 2026); update it when the team changes. The
frontend reads it when its container starts, so a change needs a staging
redeploy (run the Deploy workflow by hand on `dev`). `STAGING_OTP` holds the code; with it
unset, the page is Clerk's normal one.

It works by minting a Clerk sign-in ticket for the email, which is only safe
because staging uses Clerk's **development** instance. A session on the
production instance would also be a session on `admin.gdg-q.com`, so the code
refuses to run with a live (`sk_live_`) key even if `STAGING_OTP` is set.

The development instance's session token has no email claim, so the route
writes the email into the user's `publicMetadata`; the backend's
`resolve_member` finds the member by `metadata.email` and stores the new Clerk
id on the `scores_staging` row.

## What staging still shares with production

Same Google account for Forms, same Cloudflare R2 bucket, same wallet signing
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
