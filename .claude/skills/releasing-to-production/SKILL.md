---
name: releasing-to-production
description: Releases GDG's dev branches to main, which deploys production, across Backend, Admin and Website in the right order. Use only when Albrrak773 asks to release, ship to production, promote dev to main, or open a "dev -> main" PR. Not for ordinary feature PRs, which go into dev.
---

# Releasing dev to main

A push to `main` deploys production: Backend runs `alembic upgrade head` on the `scores` database, then
restarts. Admin and Website rebuild their containers. The timing is the team's call, so open release
PRs only when asked, and merge only when told to.

Work from the folder that holds the repos side by side (`gdg-backend`, `gdg-admin-app`,
`gdg-members-app`). Run each step in every repo that has something to release.

```
- [ ] 1. See what each repo would release
- [ ] 2. Staging is green for exactly that dev commit
- [ ] 3. Open one dev -> main PR per repo
- [ ] 4. Merge Backend, wait for its production deploy
- [ ] 5. Merge Admin and Website, wait for their deploys
- [ ] 6. Smoke-check production
```

## 1. What would ship

```bash
git -C <repo> fetch origin
git -C <repo> log --oneline origin/main..origin/dev      # empty: nothing to release
git -C <repo> diff --stat origin/main...origin/dev
```

For Backend, also list the migrations that will run on production:

```bash
git -C gdg-backend diff --name-only origin/main...origin/dev -- alembic/versions
```

If one of them moves data, stop and confirm with Albrrak773 that it was dry-run against a copy of
production (the `writing-alembic-migrations` skill, section 5). Staging is an older copy.

## 2. Staging must be green for that commit

```bash
gh -R Google-Developer-Group-Qassim-Unversity/<Repo> run list --workflow deploy.yml --branch dev --limit 1 \
  --json headSha,conclusion,displayTitle
```

`headSha` must equal `git rev-parse origin/dev` and `conclusion` must be `success`. A failed staging
deploy means the same failure is waiting on production: fix it through a PR into `dev` first.

## 3. Open the PRs

One PR per repo, head `dev`, base `main`. Use the `writing-prs` skill for the text. The description
lists the PRs it ships (`#41`), the migrations, and the merge order. Example title:
`Release: bonuses get their own model`.

```bash
gh -R Google-Developer-Group-Qassim-Unversity/<Repo> pr create --base main --head dev --title "..." --body-file <file>
```

## 4-5. Merge in order (only when told to)

Merge with a **merge commit**, never squash: `main` must keep `dev`'s commits, or the next release
shows them again as new.

```bash
gh -R Google-Developer-Group-Qassim-Unversity/Backend pr merge <n> --merge
gh -R Google-Developer-Group-Qassim-Unversity/Backend run list --workflow deploy.yml --branch main --limit 1
```

Backend goes first. Wait until its `main` deploy succeeds, then merge Admin and Website, whose new code
calls the new API. If the Backend deploy fails, don't merge the others; read the run's log
(`gh run view <id> --log-failed`) and report it. A failed migration stops the deploy before the swap,
so the old release keeps serving. A failed smoke test happens after the swap, so production is down:
say so at once.

If an iOS build depends on the new API, tell Azzam to distribute it only after the Backend production
deploy succeeds.

## 6. Smoke-check

```bash
curl -fsS https://refactor.albrrak773.com/health/db
curl -fsS -o /dev/null -w "%{http_code}\n" https://gdg-q.com/
curl -fsS -o /dev/null -w "%{http_code}\n" https://admin.gdg-q.com/
```

When something is wrong in production, use `gdg-backend/docs/LOGGING.md` and Sentry (`sentry-gdg`).
