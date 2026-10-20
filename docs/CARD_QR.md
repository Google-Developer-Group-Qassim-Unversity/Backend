# The member card QR

Every member card shows a QR: the member app's card, the Website's card
(`gdg-q.com/wallet`) and the Apple and Google Wallet passes. Staff scan it at
the door with the Admin's camera scanner (`POST /attendance/{event_id}/scan`).

## What it encodes

```
https://gdg-q.com/p/{uuid}?s={sig}
```

- `uuid` is the member's card id (`member_profiles.uuid`, 32 hex characters),
  the same id as the public profile page `/p/{uuid}`.
- `sig` is the first 16 characters of
  `base64url(HMAC-SHA256(CARD_QR_SECRET, "card:v1:" + uuid))` (96 bits),
  made by `app/services/card_qr.py`.

The public profile link is meant to be shared, so before the signature anyone
could turn a member's `/p/` link into a QR and be checked in as them. A QR
without the right `s` is now recognisable. A screenshot of a member's own card
still works: the value is static, because a Wallet pass cannot change after it
is added (that would need a PassKit web service).

The 71-character value is a version 5 QR (37×37) instead of version 4; every
client draws any size. Sharing a profile keeps the unsigned `/p/{uuid}` link,
so the signature is never posted anywhere.

## Where it comes from

| Place | Value |
|---|---|
| `GET /wallet/me` | `qr_value` (top level). `null` for a signed-in caller who has not onboarded |
| `POST /wallet/apple-pass`, `POST /wallet/google-pass` | members: the same `qr_value` in the pass's QR. Guests: the home page, `https://gdg-q.com`, and a random uuid that never comes from the request |
| Member app, Website card | `qr_value` when the API sends it, else the unsigned link (older backends) |

Without `CARD_QR_SECRET` the value is the unsigned link every card carried
before, so the routes behave exactly as they did.

## The scan route

The Admin scanner reads `s` from the scanned URL and sends it as `sig` with the
`uuid`:

| Request | `CARD_QR_SECRET` set | Answer |
|---|---|---|
| `sig` that verifies | yes | as before (`marked` / `already_marked`) |
| `sig` that does not verify | yes | 403 `card_signature_invalid`, nothing recorded |
| no `sig` | yes, `ATTENDANCE_REQUIRE_CARD_SIGNATURE=false` (default) | as before |
| no `sig` | yes, `ATTENDANCE_REQUIRE_CARD_SIGNATURE=true` | 403 `card_signature_required` |
| anything | no | as before: nothing can be verified, and enforcement is ignored (logged as a warning) |

An older Admin that sends no `sig` keeps working until enforcement is on; an
older backend ignores the `sig` key.

## Rollout

1. Merge the Backend change to `dev` and set `CARD_QR_SECRET` for staging
   (Infisical `/admin-backend`, a new random value: `openssl rand -base64 48`).
   Enforcement stays off.
2. Merge the Admin and Website changes; the member app 1.0.2 reads `qr_value`.
3. `dev` → `main` with `CARD_QR_SECRET` set for production (its own value).
4. Members re-add their Wallet passes, which never update by themselves:
   announce it. The app's and the website's cards update on their own.
   (Google Wallet may keep an already-saved object for the same id; check a
   re-added Google pass shows the `?s=` value before step 5.)
5. Only then (suggested: 30 days later) set
   `ATTENDANCE_REQUIRE_CARD_SIGNATURE=true`. From that moment every older pass
   and every screenshot of an unsigned QR is refused at the door; the member
   shows the card from the app or the website instead, or staff mark them by
   hand.

Steps 1–3 can happen in any order. Only step 5 breaks anything.

Changing `CARD_QR_SECRET` later invalidates every issued card at once (with
enforcement on, nobody's pass scans until it is re-added), so treat it like a
signing key: set it once per environment.
