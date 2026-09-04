# Environment Variables

This project uses [Infisical](https://infisical.com/) for secrets management, with support for local overrides via `.env.local` during development.

This document explains how environment variables are loaded and managed in this project

## Reference

Every variable the backend reads is declared in `app/config.py` as a field on
`Settings`. That class is the single source of truth - this table is generated
from it, so if you add a variable there, add a row here.

Nothing is required at import time. Settings are read when a feature needs them,
so an instance without R2 or Wallet credentials still boots and serves
everything else. The two exceptions are checked by the app's `lifespan` and fail
the boot rather than the first request.

### Core

| Variable | Needed | Default | Notes |
|---|---|---|---|
| `ENV` | optional | `Production` | `development` uses the local log dir and tags Sentry as dev |
| `LOG_LEVEL` | optional | `INFO` | `DEBUG` also logs request bodies, which contain member PII |
| `SENTRY_DSN` | optional | - | leave unset to disable Sentry |
| `DATABASE_URL` | **at startup** | - | the app refuses to start without it |
| `CLERK_JWKS_URL` | **at startup** | - | the app refuses to start without it |
| `JWT_SECRET` | when the feature runs | - | signs attendance QR tokens |

### Google Forms sync

| Variable | Needed | Default | Notes |
|---|---|---|---|
| `GOOGLE_CLIENT_ID` | when the feature runs | - | must match the Frontend's value - see below |
| `GOOGLE_CLIENT_SECRET` | when the feature runs | - | must match the Frontend's value - see below |

Only the OAuth client credentials live here. Everything else about this
integration - the OAuth flow itself, the Drive template copy, the Pub/Sub
watch registration, and the Forms publish/unpublish calls - runs in the
**Frontend**, not here (`Frontend/app/api/auth/google/*`,
`Frontend/app/api/drive/*`, `Frontend/lib/google-api.ts`). This backend only
receives the Pub/Sub push webhook and does read-only Forms API calls with a
refresh token stored in the `forms.google_refresh_token` DB column.

The Frontend has three more variables for this integration, declared in
`Frontend/lib/config-server.ts` and set under its own Infisical path
(`/admin-frontend`), not documented in this file's table since it's the
Backend's reference:

| Variable | Notes |
|---|---|
| `GOOGLE_REDIRECT_URL` | this app's own OAuth callback URL, registered as an authorized redirect URI on the Google OAuth client |
| `GOOGLE_FORMS_TOPIC_NAME` | full Pub/Sub topic name (`projects/<id>/topics/<name>`) that Forms watches publish to |
| `TEMPLATE_FORM_FILE_ID` | Drive file ID of the template form every event's form is copied from |

`GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` must be the **same** values in both
Backend and Frontend: Frontend's OAuth flow issues the refresh token, and this
backend refreshes it later using the same client credentials.

### Outbound services

| Variable | Needed | Default | Notes |
|---|---|---|---|
| `CERTIFICATE_API_URL` | when the feature runs | - |  |
| `MEMBER_APP_URL` | when the feature runs | - |  |
| `MEMBER_APP_REVALIDATE_SECRET` | when the feature runs | - | bearer token for the leaderboard cache reset |
| `SES_FROM_ADDRESS` | when the feature runs | - |  |

### Cloudflare R2 (uploads)

| Variable | Needed | Default | Notes |
|---|---|---|---|
| `R2_ACCOUNT_ID` | when the feature runs | - |  |
| `R2_ACCESS_KEY_ID` | when the feature runs | - |  |
| `R2_SECRET_ACCESS_KEY` | when the feature runs | - |  |
| `R2_BUCKET_NAME` | when the feature runs | - |  |
| `R2_PUBLIC_URL` | when the feature runs | - |  |

### Apple Wallet

| Variable | Needed | Default | Notes |
|---|---|---|---|
| `APPLE_TEAM_ID` | optional | `7NN7W24VXR` | ⚠️ **hardcoded production identifier** - see the TODO in `app/config.py` |
| `APPLE_PASS_TYPE_ID` | optional | `pass.pass.com.gdg-q.wallet` | ⚠️ **hardcoded production identifier** - see the TODO in `app/config.py` |
| `APPLE_P12_BASE64` | optional | - | or supply the file at `APPLE_P12_PATH` |
| `APPLE_P12_PASSWORD` | when the feature runs | - |  |
| `APPLE_P12_PATH` | optional | - | falls back to `app/certificates/Certificates.p12` |
| `APPLE_WWDR_BASE64` | optional | - | or supply the file at `APPLE_WWDR_PATH` |
| `APPLE_WWDR_PATH` | optional | - | falls back to `app/certificates/AppleWWDRCAG4.cer` |

### Google Wallet

| Variable | Needed | Default | Notes |
|---|---|---|---|
| `GOOGLE_WALLET_ISSUER_ID` | optional | `BCR2DN6DTK643EAC` | ⚠️ **hardcoded production identifier** - see the TODO in `app/config.py` |
| `GOOGLE_WALLET_CLASS_ID` | optional | empty | defaults to `<issuer id>.gdgq-card` |
| `GOOGLE_WALLET_SERVICE_ACCOUNT_EMAIL` | when the feature runs | empty |  |
| `GOOGLE_WALLET_PRIVATE_KEY` | when the feature runs | empty |  |

### A note on the Wallet defaults

`APPLE_TEAM_ID`, `APPLE_PASS_TYPE_ID` and `GOOGLE_WALLET_ISSUER_ID` fall back to
this club's real production identifiers. That is deliberate for now - it is how
the code behaved before these variables were centralised - but it means a
deployment that forgets them signs passes with production identity instead of
failing. `app/config.py` carries a TODO with the full context and what removing
them requires.

## How It Works

### Priority Order (highest to lowest)

1. **`.env.local`** - Local overrides for development
2. **Infisical cloud secrets** - Shared team secrets

When running `run.sh`, the command:

```bash
infisical run --path=/admin-backend --env=dev -- uv run uvicorn app.main:app --reload --port 7001 --workers 2
```

1. Fetches secrets from Infisical cloud
2. Starts the server
3. the server loads `.env.local` and **overrides** any conflicting values

## Setup

### 1. Install Infisical CLI

```bash
# Windows
winget install infisical

# macOS/Linux
brew install infisical/get-cli/infisical

# Arch Linux
yay -S infisical-bin

# Ubuntu
# 1. Add Infisical repository
curl -1sLf 'https://artifacts-cli.infisical.com/setup.deb.sh' | sudo -E bash
# 2. install CLI
sudo apt-get update && sudo apt-get install -y infisical

# Or visit https://infisical.com/docs/cli/overview

```

### 2. Login to Infisical

1. `infisical login`
2. Select ` ▸ Self-Hosting or Dedicated Instance`
3. Select ` ▸ Add a new domain`
4. add `https://infisical.albrrak773.com`

### 3. Create Local Overrides (Optional)

Create a `.env.local` file in the project root:

```env
# Override backend API URL for local development
NEXT_PUBLIC_BACKEND_API_URL=https://your-local-url.com
```

## Infisical Dashboard

you can see and manage the secrets in the [link here](https://infisical.albrrak773.com/organizations/de21a8c1-87e7-4f92-9e3b-253791905f8e/projects/secret-management/300b1e97-7e52-4a4e-872d-053b9082cac5/overview)
