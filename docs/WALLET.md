# Apple Wallet pass updates and the welcome from the card

A member's Apple Wallet card can talk back to this backend through Apple's
PassKit web service ("Wallet Passes web service"). The first use is a welcome:
a few seconds after a member adds their card, Wallet shows a notification from
the card itself (the card's icon, "GDG Qassim"):

> أهلًا بك في GDG Qassim! متحمسين نشوفك قريب في فعالياتنا

The line then stays on the back of the card under «رسالة النادي». It works for
cards added from the app (any version, share sheet included) and from
gdg-q.com/wallet, and does not need the app's notification permission. Arabic
only, like the rest of the card. No app change.

Code: `app/services/wallet_updates.py` (the rules), `app/services/wallet_push.py`
(the push), `app/DB/wallet_passes.py`, the routes at the end of
`app/routers/wallet.py`, the pass fields in `app/wallet_signer.py`.

```
member adds the card ──► Wallet: POST /wallet/apple/v1/devices/{device}/registrations/{pass type}/{serial}
                              │  first registration of this member ever?
                              ├─ yes: member_profiles.pass_welcomed_at, pass_updated_at = now
                              │       registration.push_due_at = now ──► after the response:
                              │       APNs (Pass Type ID certificate, topic = pass type id, payload {})
                              ▼
        Wallet: GET .../registrations/{pass type}?passesUpdatedSince=  ──► the serial
        Wallet: GET /wallet/apple/v1/passes/{pass type}/{serial}       ──► the pass, «رسالة النادي» = the welcome
        Wallet sees the field change (changeMessage "%@") ──► notification from the card
```

## Off until configured

Two settings, both needed ([ENVIRONMENT_VARIABLES.md](ENVIRONMENT_VARIABLES.md)):

| Setting | Value |
|---|---|
| `APPLE_PASS_WEB_SERVICE_URL` | production: `https://api.gdg-q.com/wallet/apple` (once the team confirms that host serves production; see below). Staging: `https://api-dev.gdg-q.com/wallet/apple` |
| `APPLE_PASS_AUTH_SECRET` | `openssl rand -base64 48`, a different value per environment |

Either one unset: `config.apple_pass_updates_enabled` is false, passes are
exactly what they were before (no `webServiceURL`, no `authenticationToken`, no
«رسالة النادي»), the five routes answer 404 and nothing is pushed. Turning it
on is setting both and restarting.

**The URL is baked into every card issued while it is set** and cannot be
changed for cards already in Wallet (only a re-add gives a card the new one).
Pick the long-term host: `refactor.albrrak773.com` is a developer's personal
domain; `api.gdg-q.com` resolves to the same server.

## What a member pass carries

Only member passes, never guest passes (a guest's uuid is random per download,
so every guest card would be a new card).

| Key | Value |
|---|---|
| `serialNumber` | unchanged: `GDGQ-` + the first 8 characters of `member_profiles.uuid`, uppercased. A new format would put a second card in every member's Wallet |
| `webServiceURL` | `APPLE_PASS_WEB_SERVICE_URL`, without a trailing slash |
| `authenticationToken` | `base64url(HMAC-SHA256(APPLE_PASS_AUTH_SECRET, "pass:v1:" + uuid))`, 43 characters |
| back field `club_message` | label «رسالة النادي», `changeMessage` `%@`. Before the welcome: «رسائل النادي تظهر هنا»; after it: the welcome line (`GDG Qassim` wrapped in Unicode isolates, as in `app/services/push/messages.py`) |

The token is not stored. The server makes it again from the card's uuid and
compares in constant time, so a copy of the database (staging is one, backups
are others) holds no working token, and nothing is written when a pass is
issued. Changing `APPLE_PASS_AUTH_SECRET` stops every issued card from updating
until it is re-added: set it once per environment, like `CARD_QR_SECRET`.

A serial alone never names a member: two members' uuids can share their first 8
characters. The serial narrows it to those profiles; the token picks the one.

## Routes

All under `/wallet/apple/v1`, called by Wallet, never by the apps. All answer
404 while the feature is off.

| Route | Auth | Answers |
|---|---|---|
| `POST /devices/{device}/registrations/{pass type}/{serial}`, body `{"pushToken"}` | `ApplePass <token>` | 201 new, 200 already registered, 401, 422 bad body |
| `DELETE /devices/{device}/registrations/{pass type}/{serial}` | `ApplePass <token>` | 200 (also when it was not registered), 401 |
| `GET /devices/{device}/registrations/{pass type}?passesUpdatedSince={tag}` | none: the device library id is its secret (Apple's design) | 200 `{"serialNumbers", "lastUpdated"}`, 204 nothing changed |
| `GET /passes/{pass type}/{serial}` | `ApplePass <token>` | 200 the pass, signed now, with `Last-Modified`; 304 for `If-Modified-Since` at or after it; 401 |
| `POST /log`, body `{"logs": [...]}` | none | 200; up to 10 lines of 500 characters kept as WARNING, device ids hidden |

401 covers a wrong or missing token, an unknown serial and another pass type
alike, so the answer never tells which serials exist. The update tag
(`lastUpdated`) is `member_profiles.pass_updated_at` in whole seconds since the
epoch; only the welcome bumps it today.

`tests/test_route_auth.py` pins the three token routes as `apple_pass_token` and
the other two as public.

## Tables

| Table / column | |
|---|---|
| `wallet_pass_registrations` | one device holding one pass: `device_library_id`, `push_token` (the pass's own APNs token, lowercase hex), `serial_number`, `member_id` (the member whose token the device presented), `created_at`, `updated_at` (last registration; Wallet registers again with a new token after a restore), `push_due_at` / `push_attempts` while a push is owed. Unique per device and serial; at most 10 per member (the oldest goes) |
| `member_profiles.pass_welcomed_at` | NULL until one of the member's cards first registers. Set once, under the row's lock (`IS NULL` in the `UPDATE`), so two devices at once welcome once |
| `member_profiles.pass_updated_at` | the pass's update tag. Setting it leaves `member_profiles.updated_at` alone (it means the member's last card edit) |

Revision `194e7997ddc9`. Deleting an account (`anonymize_member`) deletes the
member's registrations, and the profile with its two columns; the old card then
gets 401.

## The welcome, once per member

- First registration ever: welcome, bump the tag, push to that device right
  away, quiet hours or not (the member just tapped Add).
- Another device later, a re-added card, a new iPhone: the pass they download
  already carries the welcome, so nothing changes and nothing is announced. No
  push.
- Another member: their own welcome.
- Cards added before the settings were set have no `webServiceURL` and never
  call. Adding the card again (same serial: Wallet replaces it) makes it live,
  and that member gets the welcome then.
- A paired Apple Watch that registers on its own: the push goes only to the
  device that registered first (the iPhone, in practice), whose notification
  shows on the watch as usual. The watch's copy keeps the placeholder until a
  later update.
- Wallet's per-card "Allow Notifications" off (it is on by default), or Wallet
  notifications off in iOS: the line still lands on the back, silently.
- A push that never arrives (given up after 5 attempts): the welcome shows the
  next time Wallet fetches the pass, after a later update or a manual refresh.

## The push

APNs, certificate-based: the Pass Type ID certificate and key, the same p12
that signs the passes (`APPLE_P12_BASE64` or `APPLE_P12_PATH`, with
`APPLE_P12_PASSWORD`). The app's `.p8` key cannot do it (it is limited to
`com.gdgq.app`). `apns-topic` is `APPLE_PASS_TYPE_ID`, the payload is `{}`, the
host is always `api.push.apple.com` (Apple delivers pass updates in production
only), no `apns-push-type`, default priority, expiration one day.

Python's `ssl` loads a client certificate only from a file, so the key is
written encrypted with a one-time password that exists only in memory, to a
memfd on Linux (RAM, no path on any disk) or, elsewhere, a 0600 temporary file
deleted as soon as it is loaded. One HTTP/2 client per worker, built on the
first push.

`wallet_push.deliver_due()` runs right after the registration that owes a push
(`BackgroundTasks`) and on every worker after each pipeline sweep, in the steps
of [PUSH.md](PUSH.md#delivery): claim (`FOR UPDATE SKIP LOCKED`, a 5-minute
lease), send, record. The registration's answer never waits for it or depends
on it.

| APNs answer | What happens |
|---|---|
| 200 | nothing owed any more |
| a dropped connection, a timeout | tried once more at once, then as below |
| 5xx, 429, 403 / bad certificate or topic, no usable p12 | retried after 2, 4, 8, 16 minutes (the next sweep after that); given up after 5 attempts. Certificate and topic errors are one ERROR per batch |
| 410 `Unregistered`, 400 `BadDeviceToken` / `DeviceTokenNotForTopic` | the registration is deleted, unless the device registered again since (a new token, or after the moment APNs gives for a 410): then retried as above, with whatever token it has now, and given up after 5 attempts |
| other 4xx | given up, with Apple's reason in the log |

Logs name the registration row and the member id, never a token. The device
library id is in the request paths, so the request log line, the error
handler's and uvicorn's access line read `/v1/devices/[device]`; Sentry events
lose `pushToken` values and any local named like `token` or `authorization`
(`app/services/push/redact.py`).

## Deploying

1. Merge the Backend PR into `dev`. The deploy runs the migration on
   `scores_staging`; nothing changes while the settings are unset.
2. Check the Pass Type ID certificate inside `APPLE_P12_BASE64` (Infisical
   `/admin-backend`): it must be the certificate of `pass.pass.com.gdg-q.wallet`
   (it already signs the passes) and not about to expire. Its end date:
   `openssl pkcs12 -in Certificates.p12 -nokeys | openssl x509 -noout -subject -enddate`
   (add `-legacy` after `pkcs12` if OpenSSL 3 refuses the file; on a machine
   where the file may be, never in a shared log). When it expires, both new
   passes and pushes stop.
3. The servers must reach `api.push.apple.com:443` (outbound HTTPS/2), and
   whatever sits in front of the API must let Wallet's requests through with
   their `Authorization` header (`ApplePass ...`) untouched.
4. Staging (Infisical env `staging`, `/admin-backend`):
   `APPLE_PASS_WEB_SERVICE_URL=https://api-dev.gdg-q.com/wallet/apple` and a new
   `APPLE_PASS_AUTH_SECRET`. Redeploy. Check:
   `curl -i https://api-dev.gdg-q.com/wallet/apple/v1/devices/test/registrations/pass.pass.com.gdg-q.wallet`
   answers 204 (404 means off).
5. Test on a real iPhone (below).
6. Production, after the `dev` -> `main` merge: confirm `api.gdg-q.com` serves
   production, then set `APPLE_PASS_WEB_SERVICE_URL=https://api.gdg-q.com/wallet/apple`
   and its own `APPLE_PASS_AUTH_SECRET` in the `prod` env, and redeploy. The same
   `curl` against `api.gdg-q.com` answers 204.

## Testing on a real iPhone

The simulator has no Wallet, and the URL must be HTTPS and reachable from the
phone, so test against staging.

Staging is a copy of production with the same pass type id and signing
identity: a staging card has the same serial as your production card, so it
**replaces** it on the phone. Add the production card again when done.

1. Get a staging pass for a test member. Sign in on `admin-dev.gdg-q.com` (any
   email + `8888`), copy the session token (the `__session` cookie), and
   `curl -X POST https://api-dev.gdg-q.com/wallet/apple-pass -H "Authorization: Bearer <token>" -o card.pkpass`.
   AirDrop or mail `card.pkpass` to the iPhone and add it.
2. Within seconds: a notification from the card with the welcome line. On the
   back of the card (⋯ → Pass Details), «رسالة النادي» shows it.
3. The staging logs (`pm2 logs GDG-backend-staging`, see [LOGGING.md](LOGGING.md))
   show `POST /wallet/apple/v1/devices/[device]/registrations/... -> 201`,
   `first Wallet pass registration`, `Wallet pass push sent to registration ...`,
   then Wallet's `GET .../registrations/...` and `GET /wallet/apple/v1/passes/... -> 200`.
4. Remove the card: `DELETE ... -> 200`. Add it again: `-> 201`, no
   notification, no push (welcomed once).
5. Nothing shows? Check the card's "Allow Notifications", then `POST
   /wallet/apple/v1/log` lines in the log (Wallet reports what failed). On a Mac,
   Console.app with the phone connected, filtered on `passd`, shows Wallet's
   side.

For a member who was welcomed already, the test needs a member whose
`pass_welcomed_at` is NULL (a new staging member, or that column cleared on
`scores_staging` for your own row).

## Tests

`tests/routers/test_wallet_passes.py` (the routes, the welcome, retries, logs,
deletion), `tests/test_wallet_push.py` (the certificate client, the request,
redaction), `tests/test_apple_wallet_pass.py` (the pass fields). No test calls
Apple: pushes go to `httpx.MockTransport`, the certificate is self-signed.
