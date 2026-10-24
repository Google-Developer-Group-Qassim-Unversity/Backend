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
| `ENV` | optional | `Production` | `Production`, `Staging`, `development` or `testing`; tags Sentry with it. `Staging` and `development` redirect every email - see `EMAIL_REDIRECT_FALLBACK` |
| `LOG_LEVEL` | optional | `INFO` | `DEBUG` also logs request bodies, which contain member PII |
| `SENTRY_DSN` | optional | - | leave unset to disable Sentry |
| `DATABASE_URL` | **at startup** | - | the app refuses to start without it |
| `CLERK_JWKS_URL` | **at startup** | - | the app refuses to start without it |
| `CLERK_SECRET_KEY` | for the member app's account routes | - | the same Clerk instance's secret key (`sk_live_...` in prod; the development instance's `sk_test_...` on staging and local dev). `POST /members/onboarding` and `DELETE /members/me` read the caller's verified emails, write their publicMetadata and delete their sign-in with it (`app/services/clerk_api.py`); unset, or refused by Clerk, both answer 503 `clerk_not_configured`. The scripts in `scripts/` read it too |
| `APPLE_SIGNIN_KEY_ID` | for Apple token revocation | - | Key ID of a Sign in with Apple key (Apple Developer → Keys, "Sign in with Apple" enabled for the app's App ID) in the `APPLE_TEAM_ID` account. With `APPLE_SIGNIN_PRIVATE_KEY`, `DELETE /members/me` revokes the app's Apple tokens with the code the member app sends (`app/services/apple_signin.py`); either unset: the deletion goes ahead, `apple_token_revoked` is `false` and the app shows Apple's manual steps |
| `APPLE_SIGNIN_PRIVATE_KEY` | for Apple token revocation | - | that key's `.p8` contents (PEM). Newlines may be written as `\n`. Never logged |
| `APPLE_SIGNIN_PRIVATE_KEY_PATH` | optional | - | or the `.p8` file's path, when `APPLE_SIGNIN_PRIVATE_KEY` is unset |
| `APPLE_SIGNIN_CLIENT_ID` | optional | `com.gdgq.app` | the member app's App ID (bundle id): the `client_id` of the token calls and the `sub` of their client secret. Case-sensitive |
| `JWT_SECRET` | when the feature runs | - | signs attendance QR tokens |
| `CARD_QR_SECRET` | for signed card QRs | - | at least 32 random bytes, a different value per environment (`openssl rand -base64 48`). Signs the member card QR: `/wallet/me`'s `qr_value` and the Wallet passes carry `https://gdg-q.com/p/{uuid}?s={sig}` (`app/services/card_qr.py`, [CARD_QR.md](CARD_QR.md)). Unset: the unsigned link, as before. Changing it invalidates every card already issued |
| `ATTENDANCE_REQUIRE_CARD_SIGNATURE` | optional | `false` | `true`: staff card scans (`POST /attendance/{id}/scan`) refuse a QR without a valid signature (403 `card_signature_required`). Breaks every older pass and screenshot at the door: turn on only after the rollout in [CARD_QR.md](CARD_QR.md). Ignored while `CARD_QR_SECRET` is unset |

### Google Forms sync

| Variable | Needed | Default | Notes |
|---|---|---|---|
| `GOOGLE_CLIENT_ID` | when the feature runs | - |  |
| `GOOGLE_CLIENT_SECRET` | when the feature runs | - |  |
| `GOOGLE_REFRESH_TOKEN` | when the feature runs | - | the club's Google account, minted once by `scripts/setup_google_oauth.py` - see `docs/GOOGLE_FORMS.md` |
| `TEMPLATE_FORM_FILE_ID` | when the feature runs | - | Drive file ID of the template form copied for each event |
| `GOOGLE_FORMS_TOPIC_NAME` | when the feature runs | - | Pub/Sub topic used for Forms response-change watches |
| `GOOGLE_ALLOWED_EMAIL_DOMAINS` | optional | `gmail.com,googlemail.com` | comma-separated allow-list checked before inviting an admin to a form |
| `GOOGLE_CALENDAR_ID` | when the feature runs | - | Google Calendar ID for events (read-only) |
| `GOOGLE_ORGANIZER_EMAIL` | when the feature runs | - | GDG organizer Google account email address |
| `APP_TIMEZONE` | optional | `Asia/Riyadh` | Club and event display timezone |

Everything about this integration - the one-time club OAuth, the Drive
template copy, the Pub/Sub watch registration, and the Forms publish calls -
runs here in the Backend now. There is no frontend-side OAuth flow anymore
(see `docs/GOOGLE_FORMS.md`). The admin frontend keeps its own copy of
`TEMPLATE_FORM_FILE_ID` (declared in `Admin/lib/config-server.ts`, set
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
| `ADMIN_APP_URL` | no | `https://admin.gdg-q.com` | links in events pipeline emails |
| `NOTION_TOKEN` | for Design's Notion board | - | internal integration secret (`ntn_...`) of the design team's Notion; with `NOTION_DESIGN_DATABASE_ID`, every request that reaches Design gets a card there. Unset: nothing is sent. Setup steps are on Notion (Events pipeline → Design's Notion board) |
| `NOTION_DESIGN_DATABASE_ID` | for Design's Notion board | - | the database (board) the cards go in; shared with the integration. One board for prod and staging; staging titles carry a `(staging)` prefix |
| `GOOGLE_CALENDAR_ID` | for the team's Google Calendar | - | the calendar every event request goes on, from booking to the published event (Google Calendar → the calendar's settings → Integrate calendar → Calendar ID). With `GOOGLE_CALENDAR_REFRESH_TOKEN`; either unset: nothing is sent. Give staging its own calendar - entry ids carry the environment, so the two never overwrite each other, but staging's entries would sit next to prod's |
| `GOOGLE_CALENDAR_REFRESH_TOKEN` | for the team's Google Calendar | - | refresh token of the account that can edit that calendar (a person's own account, not the club's), `calendar.events` scope only. Mint it with `infisical run --env=dev --path=/admin-backend -- uv run python scripts/setup_google_oauth.py --calendar` |
| `GOOGLE_CALENDAR_CLIENT_ID` | for the team's Google Calendar | `GOOGLE_CLIENT_ID` | the calendar's own OAuth client (Desktop app), in a Google Cloud project of that account, **published** (in production) so the token does not expire after 7 days like the club's testing client's. `calendar.events` is sensitive, not restricted: no verification needed, one "unverified app" screen when approving |
| `GOOGLE_CALENDAR_CLIENT_SECRET` | for the team's Google Calendar | `GOOGLE_CLIENT_SECRET` | that client's secret |
| `GOOGLE_MEET_CLIENT_ID` | for Meet links | - | OAuth client of the club's meetings account, the one its Telegram meetings bot uses (`app/services/google_meet.py`). Not the Forms account, not the team's calendar account. With the other three, Logistics gets a "generate a link" button on its confirmation; any unset hides it |
| `GOOGLE_MEET_CLIENT_SECRET` | for Meet links | - |  |
| `GOOGLE_MEET_REFRESH_TOKEN` | for Meet links | - | granted `calendar.events`, `meetings.space.created` and `meetings.space.settings`. Shared with the live bot: never revoke it or re-run consent for it; ask for a second token instead. Refreshed without a `scope` |
| `GOOGLE_MEET_CALENDAR_ID` | for Meet links | - | the meetings account's events calendar (`...@group.calendar.google.com`) every generated link's event goes on; never taken from a request. Not `GOOGLE_CALENDAR_ID`, which is the team's calendar |
| `EMAIL_REDIRECT_FALLBACK` | on staging | - | comma-separated developer addresses. With `ENV` `Staging` or `development`, an email goes to the member who clicked send instead of its recipients; emails nobody clicked for (the pipeline sweep) go here. Unset there, those emails fail rather than reach real members - see `app/services/email_redirect.py` |

**Dev note on `MEMBER_APP_URL`:** Infisical's dev env points it at
`https://gdg-q.com`, so a cache reset from a local backend purges the real
site's cache instead of the one running on your machine. `.env.local`
therefore overrides it with the local Website app (`http://localhost:3001`) -
that file is gitignored and specific to one machine. Remove or change the
override if you run the Website elsewhere; set it before the first
content/event edit or the local Website's cache never refreshes.

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
| `APPLE_PASS_WEB_SERVICE_URL` | for Wallet pass updates | - | the PassKit web service's base URL, baked into every member pass issued while it is set: `https://<api host>/wallet/apple` (no trailing slash needed; Wallet appends `/v1/...`). With `APPLE_PASS_AUTH_SECRET`, member passes register for updates and get the welcome from the card ([WALLET.md](WALLET.md)). Unset: passes are what they were before and the `/wallet/apple/v1` routes answer 404. Hard to change later: cards already in Wallet keep the URL they were issued with |
| `APPLE_PASS_AUTH_SECRET` | for Wallet pass updates | - | at least 32 random bytes, a different value per environment (`openssl rand -base64 48`). Makes each member pass's `authenticationToken` (HMAC of the card's uuid; nothing is stored). Never logged. Changing it stops every issued card from updating until it is re-added. Unset: pass updates stay off even with the URL set |

### Google Wallet

| Variable | Needed | Default | Notes |
|---|---|---|---|
| `GOOGLE_WALLET_ISSUER_ID` | optional | `BCR2DN6DTK643EAC` | ⚠️ **hardcoded production identifier** - see the TODO in `app/config.py` |
| `GOOGLE_WALLET_CLASS_ID` | optional | empty | defaults to `<issuer id>.gdgq-card` |
| `GOOGLE_WALLET_SERVICE_ACCOUNT_EMAIL` | when the feature runs | empty |  |
| `GOOGLE_WALLET_PRIVATE_KEY` | when the feature runs | empty |  |

### Member app push notifications

The iPhone app's notifications go straight to Apple's APNs (`app/services/push/`,
[PUSH.md](PUSH.md)). With `APNS_KEY_ID` or the key unset, push is off: phones and
switches are still stored, nothing is queued or sent, and
`POST /notifications/broadcast` answers 503 `push_not_configured`. The team id is
`APPLE_TEAM_ID` above.

| Variable | Needed | Default | Notes |
|---|---|---|---|
| `APNS_KEY_ID` | to send pushes | - | Key ID of the APNs auth key (Apple Developer → Keys). Sent as the provider token's `kid` |
| `APNS_PRIVATE_KEY` | to send pushes (or the path below) | - | contents of that key's `.p8` file (PEM). Newlines may be written as `\n`. Never logged |
| `APNS_PRIVATE_KEY_PATH` | alternative to `APNS_PRIVATE_KEY` | - | path to the `.p8` file instead, for local development |
| `APNS_TOPIC` | optional | `com.gdgq.app` | the app's bundle ID |
| `APNS_ENVIRONMENT` | optional | `production` | `production` (`api.push.apple.com`: TestFlight and App Store builds) or `sandbox` (development builds). Only phones registered from builds of the same environment are sent to |

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
