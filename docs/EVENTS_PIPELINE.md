# Events pipeline

A department books dates, fills in the event and two briefs, and the request
moves Design + Logistics → Media → ready → published as a real event. The
plan and every decision are on Notion (GDG → Features → Events pipeline).

## Stages

`draft` (24-hour hold) → `in_review` (Design + Logistics) ⇄ `returned` (once,
12 hours to fix) → `media` (Design done) → `ready` (all three done) →
`published` (an open row in `events`). `cancelled` drops a draft.

## Who can do what

`app/services/department_permissions.py`. A department's current-semester
leader and VPs act for it and can grant that to its members; super admins act
for every department and skip the time rules (lockout, bans, taken days, the
4-day limit, the return window). Design, Logistics and Media are the
departments on the current semester's roster whose names contain those words,
ignoring case (`app/DB/pipeline_teams.py`); none or two matches leaves the team
unset, and submit says so. Their leaders and VPs get the team's permissions
from `TEAM_PERMISSIONS` in `app/services/permissions/catalogue.py`.

## Time

Every rule reads the clock through `app/services/event_pipeline_clock.py`
(UTC `now`, Riyadh `today`); tests freeze it. Reads never trust stale rows: an
expired hold is free the moment it expires.

The sweep (`app/services/pipeline_sweep.py`) does the side effects - marking
expired holds, growing late penalties - every 5 minutes inside the backend.
All four workers run the loop; `GET_LOCK('pipeline_sweep')` lets one sweep at
a time. `POST /pipeline/sweep` (super admin) runs it now.

## Deliverables

What a team hands back, the way a brief is what the requesting team asks for:
JSON on the team's `event_request_tasks` row (`deliverable`,
`deliverable_version`), one Pydantic model per team in
`app/services/event_deliverables.py`. A team cannot finish without it.

- **Logistics confirms the event as it was booked**: dates, each day's mode,
  daily times, venue and room, the Google Meet link for online days, the
  responsible club member (`GET /pipeline/people`), event type and
  description. It opens prefilled from the request and the Logistics brief
  (`PUT .../deliverables/logistics` saves a draft). Confirming
  (`POST .../tasks/logistics/complete`) checks it, and other dates move the
  request under the booking rules, inside the booking lock; the requesting
  department gets a `dates_changed` notification and email.
- **Design uploads the poster** (`POST .../poster`, PNG/JPEG/WebP up to
  10 MB, stored in R2 under `event-images/`). Design can replace it until the
  event is published.
- Media still finishes with "Mark done".

## Emails

`PIPELINE_EMAILS_LIVE` (default `false`). Off, a department email goes only to
the person whose action sent it (for the sweep: whoever booked), subject
prefixed `[تجربة · Trial]`, with the list of who would have received it. On,
it goes to every member of the department's current roster. Every send is an
`email_jobs` row (type `blast`) and shows in the email jobs page.

## Publish

`POST /pipeline/requests/{id}/publish` creates the event through
`app/services/events.create_full_event` - the same code as `POST /events/` -
**open**, with nothing typed again: the points tier the team picked in the
request's details (one of `COMPOSITE_ACTION_IDS`, see
`docs/HARDCODED_ACTION_IDS.md`), the when/where/description/Meet link and
responsible person from Logistics' confirmation, and Design's poster as the
image. A late penalty is taken off the department's log for that event as a
`discount` modification, once.

## Not built yet

- Brief attachments are links, not uploads.
- Design's Notion request (waiting on the shape the design team wants).
- Media's deliverables; no Google Calendar sync.
