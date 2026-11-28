# Event lifecycle: compatibility with shipped iOS builds

The lifecycle refactor (#49) replaced the four-value `events.status`
(`draft | open | active | closed`) with `status` = `draft | published | cancelled`,
a derived `phase` and the form's `registration_status`. The iOS app builds on
TestFlight (1.1.3 and older) only know the old values. Their `isPublicEvent`
shows an event only if its status is `open`, `active` or `closed`, so against
the new API they would show no events at all, and their admin portal's
`PUT /events/{id}/status` calls (`open`, `active`, `closed`) would be 422s.

These shims let the backend ship to production before the app update, in the
same way as the bonuses Phase 1 shims ([points-model.md](points-model.md)).

## How old builds are told apart

Every app build sends `X-App-Version` (since the app's first data layer,
`gdg-app` `src/api/config.ts`). The Website and the Admin never send it. A
request with a version below **1.2.0** (or a value that is not a version) is an
old build. The first app release on the new lifecycle must be 1.2.0 or later.

## What the shims do

**Answers, old builds only** (`app/legacy_app.py`, `LegacyAppMiddleware`): every
event object in a JSON answer gets its `status` in the old values. The new
fields stay; old builds ignore them.

| New model | Old status |
|---|---|
| `draft` | `draft` |
| `cancelled`, or the event has ended | `closed` |
| published, registration or Google form, registration open | `open` |
| published, registration or Google form, registration not open or closed | `active` |
| published, no registration, before the start | `open` |
| published, no registration, after the start | `active` |

`GET /events/open` keeps only the rows the old `open_events` view held: `open`
events, and running online events without a form. The new view also lists
published events whose registration is closed or not open yet, and an old build
would show a Register button on those.

The registration state comes from the event object (lists, `/events/{id}`,
`/events/open`) or from the `form` beside it (`/events/{id}/details`). The
answers of `POST /events/`, `PUT /events/{id}`, `PUT /events/{id}/status` and
`PUT /events/{id}/meeting-url` carry no form, so they fall back to the
schedule; the old app refetches the details after each of them.

**Requests, every caller** (validators in `app/routers/models.py`): the old
values are accepted and never cancel an event.

| Request | Old value | Does |
|---|---|---|
| `PUT /events/{id}/status` | `open` | publish, open registration |
| | `active` (close registration), `closed` (close the event) | publish, close registration |
| `POST /events/` | `open` | publish, registration open |
| | `active`, `closed` | publish, registration closed |
| `PUT /events/{id}` | any old value | keep the event's status (old builds send back what they read) |

## Known gaps for old builds

- An ended event reads `closed`, so an old build's admin portal hides its
  attendance buttons after the event's end. Late attendance goes through the
  Admin site.
- "Close the event" on an event that has not ended closes registration; the
  event reads back `active` until it ends.
- Old builds cannot see `not_open` registration (they see `active`), cancelled
  events (they see `closed`) or accepted-only meeting links (the link is
  withheld, as for the new clients).

## When to remove them

Once no old build is left: TestFlight builds expire 90 days after upload, so 90
days after the last pre-1.2.0 build was uploaded (and sooner if the club asks
testers to update). Remove together:

- `app/legacy_app.py` and its `app.add_middleware(LegacyAppMiddleware)` line in `app/main.py`
- `LegacyEventStatus`, `_take_legacy_status` and the `legacy_status` fields and
  validators in `app/routers/models.py`, and the two `legacy_status` branches in
  `app/routers/events.py`
- `tests/test_legacy_app.py` and this file

The bonuses Phase 1 shims have the same deadline ([points-model.md](points-model.md), Phase 2).
