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
| `GOOGLE_CLIENT_ID` | when the feature runs | - |  |
| `GOOGLE_CLIENT_SECRET` | when the feature runs | - |  |
| `GOOGLE_REFRESH_TOKEN` | when the feature runs | - | the club's Google account, minted once by `scripts/setup_google_oauth.py` - see `docs/GOOGLE_FORMS.md` |
| `TEMPLATE_FORM_FILE_ID` | when the feature runs | - | Drive file ID of the template form copied for each event |
| `GOOGLE_FORMS_TOPIC_NAME` | when the feature runs | - | Pub/Sub topic used for Forms response-change watches |
| `GOOGLE_ALLOWED_EMAIL_DOMAINS` | optional | `gmail.com,googlemail.com` | comma-separated allow-list checked before inviting an admin to a form |

Everything about this integration - the one-time club OAuth, the Drive
template copy, the Pub/Sub watch registration, and the Forms publish calls -
runs here in the Backend now. There is no Frontend-side OAuth flow anymore
(see `docs/GOOGLE_FORMS.md`). The Frontend keeps its own copy of
`TEMPLATE_FORM_FILE_ID` (declared in `Frontend/lib/config-server.ts`, set
under its own Infisical path `/admin-frontend`) purely so a super admin can
open the template from the Settings page - that file id isn't a secret, just
duplicated across both apps for that one convenience link.

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
