# Events pipeline

A department books dates, fills in the event and two briefs, and the request
moves Design + Logistics → Media → ready → published as a real event. The
plan and every decision are on Notion (GDG → Features → Events pipeline).

## Stages

`draft` (24-hour hold) → `in_review` (Design + Logistics) ⇄ `returned` (once,
12 hours to fix) → `media` (Design done) → `ready` (all three done) →
`published` (a draft row in `events`). `cancelled` drops a draft.

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

## Emails: trial mode

`PIPELINE_EMAILS_LIVE` (default `false`). Off, a department email goes only to
the person whose action sent it (for the sweep: whoever booked), subject
prefixed `[تجربة · Trial]`, with the list of who would have received it. On,
it goes to every member of the department's current roster. Every send is an
`email_jobs` row (type `blast`) and shows in the email jobs page.

## Publish

`POST /pipeline/requests/{id}/publish` creates the event through
`app/services/events.create_full_event` - the same code as `POST /events/` -
as a **draft**, with the points tier the team picks. A late penalty is taken
off the department's log for that event as a `discount` modification, once.

## Trial limits

- Brief attachments are links, not uploads (`/upload` is admin-only).
- Deliverables are a "mark done" check.
- No Google Calendar / Notion sync.
