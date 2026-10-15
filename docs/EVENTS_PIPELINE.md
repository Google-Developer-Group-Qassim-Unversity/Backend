# Events pipeline

A department books dates, fills in the event and two briefs, and the request
moves Design + Logistics → Media → ready → published as a real event. The
plan and every decision are on Notion (GDG → Features → Events pipeline).

## Stages

`draft` (24-hour hold) → `in_review` (Design + Logistics) ⇄ `returned` (once,
12 hours to fix) → `media` (Design done) → `ready` (all three done) →
`published` (an open row in `events`).

`cancelled` (`POST /pipeline/requests/{id}/cancel`): the requesting department
(or a super admin) drops a request at any stage before publish. It gives up
its dates, every team it had reached gets a notification and an email, and
Design's Notion card turns "Cancelled". It stays readable, listed under
`?stage=cancelled`, until someone deletes it for good (`DELETE
/pipeline/requests/{id}`, cancelled requests only): the row and everything
hanging off it go, the history stays with `request_deleted` as its last entry,
and the Notion card moves to the trash.

## Closed days

Logistics closes days in blocks (`booking_blocks`): one or more days, next to
each other or not, with a required reason and the member who closed them. Each
closed day is a `booking_bans` row pointing at its block. The calendar shows
every closed day's reason, block and closer. `PUT /pipeline/calendar/bans`
makes a block (a day already closed moves to it), `DELETE
/pipeline/calendar/bans` reopens single days, `PATCH` / `DELETE
/pipeline/calendar/blocks/{id}` change a block's reason or reopen it whole. A
block left with no days is deleted.

## Who can do what

`app/services/department_permissions.py`. A department's current-semester
leader and VPs act for it and can grant that to its members; super admins act
for every department and may book a banned day, change a request's dates while
it still holds them, and cancel a request at any stage before publish.

A super admin may also book over other requests (booking or re-dating), never
over a published event. `GET /pipeline/overlaps?from=&to=` says which requests
would be bumped and who each email goes to; the booking then says, in `bumps`,
what happens to each one: `reschedule` sends it back to a draft holding no
dates (`undated_reason` `overridden`; its details and briefs stay, the teams'
parts go back to briefs and Logistics' confirmation is dropped), `cancel`
cancels it. Its department gets a notification and an email either way. Every other rule binds them too: the lockout, the 4-day limit, one
live hold per department, the 24-hour hold at submit, details frozen after
submit, Design's one return within two days, and publish only once every team
is done. Design, Logistics and Media are the
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
- **What Logistics changes, it explains.** `event_deliverables.changes`
  compares the confirmation with the prefill (`CONFIRMED_FIELDS`: dates, day
  modes, times, venue, room, type, description; a blank on either side is
  not a change). Any change makes `change_reason` required
  (`confirm.change_reason` in `deliverable_missing`; the task's
  `confirmation_changes` lists the fields). The reason and the list of
  changes go to the requesting department: with the `dates_changed` email
  when the dates moved, otherwise with a `confirmation_changed` one; both
  land in the notification payload and in the history (`dates_moved`, and
  Logistics' `task_done`).
- **A Meet link can be generated** (`POST .../deliverables/logistics/meet`)
  when the confirmation has an online day, dates and times: a calendar event
  with a Meet room on the club's events calendar, through the organizer
  account in `GOOGLE_MEET_*` (`app/services/google_meet.py`; the room is set
  open to anyone with the link, hosts present, attendance report, nothing
  recorded). The link and the calendar event id are kept on the confirmation;
  generating again replaces both and deletes the old event. Confirming moves
  the calendar event to the confirmed times after the commit, best effort.
  `actions.can_generate_meet` is false, and the button hidden, until the four
  settings are set. The team's calendar entry (below) shows the link in its
  description like any other Meet link, through its own sync.
- **Design uploads the poster** (`POST .../poster`, PNG/JPEG/WebP up to
  10 MB, stored in R2 under `event-images/`). Design can replace it until the
  event is published.
- Media still finishes with "Mark done".

## Who did what

Every "who" sits next to its "when". On `event_requests`:
`requested_by`/`requested_at`, `submitted_by`/`submitted_at`,
`returned_by`/`returned_at`, `published_by`/`published_at`,
`cancelled_by`/`cancelled_at`. On each team's task: `done_by`/`done_at`.

`pipeline_history` (`app/services/pipeline_history.py`) keeps everything in
order, written in the same transaction as the change: booking, new dates,
edits, submit, return
(with the notes), resubmit (with any lateness), Logistics' confirmation edits
and date moves (from and to), every poster upload (with the one it replaced),
each team finishing, publish, cancel, bans (on the calendar and on each
request they undated), and a published event being deleted. What the sweep
does on its own has no actor. Typing into a form is one row per person per 30
minutes. `request_id` has no foreign key, so the history outlives a request
deleted with its event. The request page shows it as "Who did what".

## Emails

`PIPELINE_EMAILS_LIVE` (default `false`). Off, a department email goes only to
the person whose action sent it (for the sweep: whoever booked), subject
prefixed `[تجربة · Trial]`, with the list of who would have received it. On,
it goes to every member of the department's current roster. Every send is an
`email_jobs` row (type `blast`) and shows in the email jobs page.

## Design's Notion board

When a request reaches Design, the backend writes its card on the design
team's Notion board and keeps it in sync - returned, resubmitted, poster
uploaded, dates moved, done, published, cancelled - without blocking any step:
the transition commits, the card is written after, and the sweep retries what
failed. `app/services/pipeline_notion.py` says what a card holds; the setup steps
are on Notion (Events pipeline → Design's Notion board). Off until
`NOTION_TOKEN` and `NOTION_DESIGN_DATABASE_ID` are set.

## The team's Google Calendar

Every request is on one Google Calendar from the moment its dates are booked
(`app/services/pipeline_calendar.py`): tentative and titled `[Held]` while it
is a draft, then `[In review]`, `[Returned]`, `[Media]`, `[Ready]`, and plain
once published - the colour is the stage too. The description has who asked,
which teams are done, each day's mode, the Meet link, the poster and a link to
the request. A request with daily times is a daily entry over its days;
without, all day. Before Logistics confirms, times and venue are the ones
asked for; after publish, the entry follows the event, so edits and Meet-link
changes from the events page show up. It comes off when the request stops
holding dates (hold expired, ban, cancel) and when its event is deleted.

Same shape as the Notion card: every transition marks the request's row in
`pipeline_calendar_entries` in its own transaction, the write happens after
the commit, and the sweep retries what failed (error kept on the row). The row
has no foreign key, so it outlives a request deleted with its event. The entry
id is derived from the request id and the environment, so a write can always
be run again and staging never touches prod's entries.

It is written as the account that can edit the calendar - a person's own,
not the club's - with a token of its own, on a published OAuth client of its
own so the token does not expire weekly: `GOOGLE_CALENDAR_ID`,
`GOOGLE_CALENDAR_REFRESH_TOKEN`, `GOOGLE_CALENDAR_CLIENT_ID` and
`GOOGLE_CALENDAR_CLIENT_SECRET` (`docs/ENVIRONMENT_VARIABLES.md`). Either
unset, nothing is sent and the marks wait; setting them writes them all.

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
- Media's deliverables.
