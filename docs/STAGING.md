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
| Sign-in            | Clerk's email code         | any email + `8888`             |

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

Staging's sign-in looks and flows like production's Clerk page: email, then
"Check your email" with code boxes. No email is sent, and the code is always
`8888` (`STAGING_OTP`). The page is a redrawn copy of Clerk's screens
(`Frontend/components/staging-sign-in.tsx`, using Clerk's own wording), because
Clerk cannot be told to accept a fixed code. "Continue with Google" is real
Clerk Google sign-in.

Any email gets past the email step; there is no list to keep. What happens
next is the permissions system's call, exactly as on production: the
middleware asks `GET /access/me` and lets in only current staff (anyone on the
current semester's roster, and super admins). Everyone else lands on
"access denied". With `STAGING_OTP` unset, the page is Clerk's own.

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
