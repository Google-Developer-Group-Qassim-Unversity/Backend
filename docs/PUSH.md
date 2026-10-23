# Push notifications (member app)

The GDG Qassim iPhone app gets its notifications straight from this backend,
over Apple's APNs. No Expo push service, no Firebase. Code:
`app/services/push/`, `app/routers/notifications.py`, `app/DB/push.py`.

```
trigger (route or sweep) ──insert──► push_notifications (outbox, unique dedupe_key, not_before)
                                          │
              after commit: BackgroundTasks ┤  every 5 min: after the pipeline sweep
                                          ▼
                     deliver_pending() ─► APNs HTTP/2 (api.push.apple.com) ─► phone
                                          │
                     410 / BadDeviceToken ┴─► delete the member_devices row
```

## Off until configured

With no `APNS_KEY_ID` / `APNS_PRIVATE_KEY`, `config.push_enabled` is false:
the app's routes still store phones and switches, triggers queue nothing and
nothing is sent. Turning push on is only setting the variables
([ENVIRONMENT_VARIABLES.md](ENVIRONMENT_VARIABLES.md)) and restarting.

## Tables

| Table | One row is |
|---|---|
| `member_devices` | a phone: its APNs token (unique: a token belongs to one member at a time), `environment` (`production` for TestFlight / App Store builds, `development` for dev builds), app version and build, the app's language. At most 5 per member; unseen for 180 days → deleted by the sweep |
| `member_notification_prefs` | a member's four switches `remind`, `accept`, `points`, `news`. No row = all four on (`news` too since revision `bfc96a1c1507`, which also switched `news` on in every row saved before it) |
| `push_notifications` | the outbox and the sent log: one notification for one member, its text in both languages, `status` `pending` / `sent` / `skipped` / `failed`, `not_before`, `expires_at`. Deleted after 90 days. `kind` gained the 1.0.3 kinds in revision `80d73cc07c1a`, which also added `event_content_links.published_at` (when a link last became visible; `created_at` for the ones already published) |
| `push_broadcasts` | an organizer's announcement (`POST /notifications/broadcast`) |

Deleting an account (`DELETE /members/me`, `anonymize_member`) deletes the
member's phones, switches and every `push_notifications` row, sent ones too.

## Routes

All under `/notifications`. Member routes take the member from the session
(`CurrentMember`); a signed-in user without a member row gets the usual 404.

| Route | Who | What |
|---|---|---|
| `POST /devices` | member | `{token, platform:"ios", environment, app_version, app_build, locale}` → `{id, registered}`. Upsert by token; re-registering refreshes `last_seen_at` and moves a token signed in elsewhere. 422 for a token that is not 64-200 hex characters |
| `DELETE /devices/{device_id}` | member | `{deleted}`. The `id` from `POST /devices`, never the token (paths are logged). Only the caller's own phone |
| `GET /preferences` | member | `{remind, accept, points, news, updated_at}`; `updated_at: null` = never saved (then all four are `true`) |
| `PATCH /preferences` | member | any of the four booleans → the full object |
| `GET /me?lang=ar\|en&limit=1..100` | member | `[{id, kind, title, body, url, event_id, created_at}]`: what was sent in the last 60 days, newest first; `created_at` is when it was sent (UTC, `Z`) |
| `POST /broadcast` | `notifications.broadcast` | `{title_ar, body_ar, title_en?, body_en?, url?, audience, dry_run?}` → `{broadcast_id, recipients}` |

Broadcasts: titles ≤ 60 characters, bodies ≤ 180, trimmed, no control
characters; English is optional (English phones get the Arabic). `audience` is
`{"type":"all"}` (members with club news on) or `{"type":"event","event_id":N}`
(that event's accepted, not cancelled registrations with reminders on).
`recipients` counts only members a push reaches: the switch on and the app on
a phone in this server's APNs environment. `dry_run: true` returns that count
and sends nothing (it works with push off too). Refusals: 422
`url_not_allowed`, 429 `broadcast_limit` (3 a Riyadh day, club-wide), 503
`push_not_configured`.

`notifications.broadcast` is held by the Media team's leaders and VPs
(`TEAM_PERMISSIONS`, next to `emails.blast`) and by super admins; anyone else
gets it through the grant screens. No migration seeds it.

### App links

A push opens one of these, and nothing else (`app/services/push/links.py`; the
app keeps the same list): `gdgq://events/{id}`, `gdgq://points/history`,
`gdgq://notifications`, `gdgq://profile/my-events`, `gdgq://wallet/card`,
`gdgq://profile/certificates` (app 1.0.3; 1.0.2 opens Notifications for it).

## What sends a push

| Kind | When | To | Switch | Timing |
|---|---|---|---|---|
| `reminder` | 60 minutes before each event day starts (open or active, online or on-site events) | accepted, not cancelled registrations not yet checked in that day | `remind` | not held for quiet hours; expires when the day starts |
| `accepted` | organizers press **Send acceptance** (`POST /emails/acceptance/blasts/{id}`) | each registration that blast claims | `accept` | quiet hours; 7 days |
| `points` | staff record attendance (card scan, manual, import) or custom points | the member | `points` | quiet hours; 3 days. Not the member's own QR check-in. A deduction sends nothing |
| `new_event` | an online or on-site event that became open in the last 48 hours and has not started | members with club news on | `news` | quiet hours; at most 2 a member a Riyadh day; expires at the start |
| `broadcast` | `POST /notifications/broadcast` | the audience | `news` or `remind` | quiet hours; 2 days |
| `event_updated` | an open or active online or on-site event that has not ended gets a new start, end, place (`location`, `location_type`) or join link (`PUT /events/{id}`, `…/status`, `…/meeting-url`); only the link removed, or other fields: nothing. Also **"Back on"**: a closed or draft event made open or active again before its start, to the registrants who were sent its cancellation. Says the event as it is at delivery, not what changed: its next day that has not ended ("Day 2 of 3" when it has several), that day's date and hours, the place or Online, and "New link to join" when the link changed ("Updated: … · Jul 28 · 4:00 PM – 6:00 PM · Hall 60"). An event moved to hidden or none sends nothing | accepted, not cancelled registrations ("Back on": only those told it was cancelled) | `remind` | 2 minutes' debounce, then quiet hours; delivered by the next sweep after that, so 2-7 minutes after the change; capped; newest only; skipped when undone inside the window (`reverted`), the event ended (`event_over`), closed (`event_closed`) or left the app (`event_hidden`); expires at the event's end |
| `event_cancelled` | an `open` or `active` online or on-site event that has not started is set to `closed` or back to `draft` (same routes). Names the start that was cancelled. Deleting the draft afterwards (`DELETE /events/{id}`) keeps the cancellations still waiting (the foreign key would delete them) and sends them, opening Notifications | accepted, not cancelled registrations | `remind` | due at once and kicked by the route (no debounce), **not** held for quiet hours, not capped; newest only; expires at the planned start |
| `event_live` | an online day of an open or active event started in the last 15 minutes (sweep): every day of an online event, and the online days of a pipeline event that mixes on-site and online days (it is published on-site with the Meet link). Those come from what Logistics confirmed, the event's own source, counted by day number from the confirmed start, and go only while the event still has its Meet link (checked again at delivery: `no_link`). Saving the edit form clears the link of an event that is not online, so after an edit a mixed event sends no live push | accepted, not cancelled registrations not checked in that day | `remind` | **not** held for quiet hours, not capped; expires at that day's end |
| `registration_closing` | 24 hours before an `open` online or on-site event with a `registration` or `google` form starts, if it was already open then; sent within 6 hours of that mark or not at all (sweep) | members with club news on and no submission on the event (a cancelled or partial one counts as one) | `news` | quiet hours; capped; expires at the start |
| `certificate` | each certificate e-mail sent for an event (`POST /emails/{event_id}`, and `POST /emails/manual-certificate` for a member of an event) | that member | `points` | quiet hours; capped; 7 days |
| `event_content` | slides or a recording published on an event (`POST /events/{id}/content-links`, a draft link published with `PUT`, `POST /upload/drive-slides/`); `other` links send nothing. Attendance staff record later (scan, manual, import) brings the content published in the last 7 days (`published_at`) to those members, looked up once per request | members with attendance on the event | `remind` | quiet hours; capped; 7 days |
| `weekly_summary` | Thursday 20:00-22:00 Riyadh while a semester runs (today between its start and end dates) (sweep) | members with points this semester: "+N this week · M this semester". A point counts for the day its `members_logs` row was written (attendance: the day it records; custom points: when given). Editing a custom-points entry (`PUT /custom/members/{log_id}`) now keeps the rows of the members who stay on it, so an edit moves no points into this week | `points` | capped; 12 hours |
| `welcome` | a member's first phone registers (`POST /notifications/devices`) within 7 days of their member record's creation | that member | `news` | quiet hours; capped; 3 days |

**Quiet hours** are 22:00-08:00 Riyadh: a push queued then waits until 08:00.

**Capped**: at most 3 pushes of the capped 1.0.3 kinds (`event_updated`,
`registration_closing`, `certificate`, `event_content`, `weekly_summary`,
`welcome`) reach a member in a Riyadh day; more are `skipped` with
`daily_cap`. Pushes another worker has out for delivery (claimed, not yet
recorded) count too, and the count is taken under a lock on each member's row
(`SELECT ... FOR UPDATE SKIP LOCKED` on `members`), so two workers claiming at
the same moment cannot both pass it: a member another claim holds keeps their
capped rows for the next round. `event_live` and `event_cancelled` are
time-critical and always go. The 1.0.2 kinds keep their own rules.

**`event_updated` and `event_cancelled`: newest only, said as it is.** Each
change writes one row per registrant, keyed by the `event_history` row the
route recorded for it; an update is held back 2 minutes, a cancellation is not.
At delivery (under the member-row lock the claim already takes) only a
registrant's newest row of that kind for the event goes: an older one is
`skipped` as `superseded` when a newer one went out, is going out, or is due
within 2 minutes of it (a newer one that quiet hours hold until 08:00 does not
stop one due now). The text is written at delivery from the event as it is
then, never a diff, so a member who got an older push still ends with the
truth. The update rows remember the event as it was before their change and
which fields changed; when the event is back as it was before the oldest
waiting change, the newest is skipped as `reverted`. So quick edits and a double
submit end as one push, and a change undone inside the window as none. A row
that would expire before it is due (an update at 21:59 for an event ending at
23:00) is not written. The routes take no extra locks for this.

**One push per `dedupe_key`**, ever: `reminder:{event}:{Riyadh date}:{member}`,
`accepted:{submission}:{submitted_at epoch}`, `points:{log}:{member}:{day}`,
`points:custom:{log}:{member}`, `new_event:{event}:{member}`,
`broadcast:{broadcast}:{member}`, `event_updated:{event}:{member}:{event_history id}`
and `event_cancelled:{event}:{member}:{event_history id}` (the history row the
route recorded for the change: one per real change, the same for a replay, so a
change back to an earlier value is told again), `event_live:{event}:{Riyadh date}:{member}`,
`registration_closing:{event}:{member}`, `certificate:{event}:{member}`,
`event_content:{event}:{content link id}:{member}`,
`weekly_summary:{ISO week, 2026-W30}:{member}`, `welcome:{member}`. Triggers can
run twice (four workers, retries, the sweep) and add nothing the second time.

**Checked again at delivery**, since a row can wait until 08:00: the switch, a
phone still registered, and the cause - the registration still accepted and the
day still on the calendar, the attendance row still there, the event still
open. For the 1.0.3 kinds: `event_updated` - the newest row (`superseded`),
still accepted, the event still there and open or active (`event_closed`),
still online or on-site (`event_hidden`), not ended (`event_over`), not undone
(`reverted`), the text rewritten from the event as it is now; `event_cancelled`
- the newest row, still accepted, the event still closed or a draft
(`event_reopened`), or deleted since (then it goes as written); `event_live` - still accepted, the day
still on the calendar, not checked in since (`checked_in`), a mixed event still
with its link (`no_link`);
`registration_closing` - the event still open at the same start, still no
submission (`registered`); `certificate` - its e-mail log still there;
`event_content` - the link still there and published (`content_removed`).
Otherwise the row is `skipped` with the reason in `last_error`.

### What the 1.0.3 kinds mean here

- **Registration "closes"** at the event's start: the database has no
  registration deadline. A `registration` or `google` form takes registrations
  while its event is `open` (the app reads leaving the open view as
  "registration closed"), and organizers move it on when it starts. So the push
  goes 24 hours before the start and names the start, not "in 24 hours".
- **"Cancelled"**: there is no cancelled status. An event that was public
  (`open`, `active`) and has not started, set to `closed` or back to `draft`, is
  what cancelling looks like (only a draft can be deleted). Closing an event
  after it started is its normal end and sends nothing.
- **Joining**, for `welcome`, is the member record's creation
  (`members.created_at`). A member who linked an older record at onboarding
  (registered on the website, or added by staff) is not new and gets none.

### The 1.0.2 app and the new kinds

The 1.0.2 app (build 4) reads a `kind` it does not know as none: iOS still
shows the alert, a tap opens the push's `url` through its allow-list (an event,
the points history, Notifications for `gdgq://profile/certificates`), and the
foreground refresh only refreshes the feed. It never calls
`GET /notifications/me` (its feed is built on the phone). So the new kinds go to
every phone, whatever its `app_build`, and the feed lists them for everyone.

Times: event `start_datetime` / `end_datetime` are Riyadh wall clock; the new
columns are UTC. Attendance rows (`members_logs.date`) use the process clock and
are read through `get_effective_date`, the way attendance itself reads them.

## Delivery

`deliver_pending()` (`app/services/push/delivery.py`) runs after a route queues
a push (`BackgroundTasks`) and on every worker after each sweep:

1. Claim up to 200 due rows with `SELECT ... FOR UPDATE SKIP LOCKED`, lease them
   (`not_before` + 5 min, `attempts` + 1) and commit. No two workers take the
   same row; rows of a worker that died come back when the lease ends.
2. Send to each of the member's phones in this environment, 20 at a time, over
   one shared HTTP/2 client per worker, in the phone's language.
3. Record the outcome in a new transaction.

| APNs answer | What happens |
|---|---|
| 200 | `sent` (when at least one phone took it) |
| 410 `Unregistered` | that phone's row is deleted, unless it registered again after Apple's `timestamp` |
| 400 `BadDeviceToken`, `DeviceTokenNotForTopic` | that phone's row is deleted |
| 403 `ExpiredProviderToken` | a new provider token (one per refused token, however many requests were refused at once), and that phone is tried once more |
| other 403s, `BadTopic`, `TopicDisallowed`, no key | an ERROR once per batch; the row stays queued |
| 429 | retried after 5 minutes (`TooManyProviderTokenUpdates`) or 1 minute |
| 5xx, timeouts, a request that fails in any other way | retried after 2^attempts minutes; `failed` after 5 attempts. The rest of the batch is recorded as usual |
| 413, other 400s | `failed` with Apple's reason |

The provider token is an ES256 JWT (`kid` = `APNS_KEY_ID`, `iss` =
`APPLE_TEAM_ID`), with `iat` on a 50-minute boundary: refreshed at least hourly
and at most every 20 minutes, as Apple asks, and the same in every worker.

Logs name the outbox row and the device row, never a token, the JWT or the
key. The token is in the APNs URL, so the `httpx` log line and Sentry's
breadcrumbs and spans are rewritten to `/3/device/[token]`
(`app/services/push/redact.py`). Sentry events also lose every stack-frame
local whose name contains `token`, and the `hpack` logger stays at INFO even
with `LOG_LEVEL=DEBUG` (its DEBUG lines print the HTTP/2 headers).

The payload carries the app's keys twice, under `body` (which expo-notifications
exposes as `content.data`) and at the top level:

```json
{"aps": {"alert": {"title": "…", "body": "…"}, "sound": "default", "thread-id": "event-435"},
 "url": "gdgq://events/435", "kind": "reminder", "id": "0192…",
 "body": {"url": "gdgq://events/435", "kind": "reminder", "id": "0192…"}}
```

## Sending a test push

From a laptop, with the dev environment's secrets (it reads the same settings
and the database the app does, and prints only Apple's answer per phone):

```bash
infisical run --env=dev --path=/admin-backend -- uv run python -m app.services.push.cli test --member-id <id>
```

The key is production-only: only TestFlight and App Store builds' phones
(`environment: production`) can receive it. Development builds register with
`environment: development` and are never sent to unless `APNS_ENVIRONMENT=sandbox`.

## Tests

`tests/routers/test_notifications.py` (the routes) and `tests/test_push_*.py`
(messages, outbox, triggers with a frozen clock, delivery against
`httpx.MockTransport`). No test calls Apple.
