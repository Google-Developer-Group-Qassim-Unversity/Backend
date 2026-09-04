# Google Forms: one club account, not one per admin

Until this redesign, attaching a Google Form to an event meant the admin doing
their own OAuth against `drive` (the broad, restricted scope - full read/write/
delete on their entire Drive), `forms.body`, and `forms.responses.readonly`,
so the app could copy a template form into *their* Google account. Every admin
saw Google's "this app isn't verified, it wants access to your Drive" warning,
because Google's OAuth verification is a property of who authorizes, not of
the app in the abstract - and here, every admin authorized separately.

On top of that, the resulting refresh token was handled badly: it travelled
through a callback URL query string, `localStorage`, an httpOnly cookie, and a
plaintext `forms.google_refresh_token` column, with no encryption anywhere.

## The fix: one identity, authorized once

A single Google account - the club's own Gmail - owns every form. It
authorizes **once**, outside the running app entirely, via
`scripts/setup_google_oauth.py` run locally by whoever administers that
account. The resulting refresh token goes into Infisical as
`GOOGLE_REFRESH_TOKEN` (`/admin-backend`, both `dev` and `prod`) - never into
the database, never into a file. Because only that one account ever completes
OAuth, and it's controlled by the club rather than by an individual admin,
no admin sees a consent screen again.

The scope for that one grant is still the full `drive` scope, not the
narrower `drive.file` - `drive.file` only sees files this OAuth client
created or the user explicitly picked through it, so it can't
`files.copy()` a pre-existing template file id at all (it 404s as if the
file doesn't exist, regardless of whether the club account can otherwise
open it). Full `drive` is what makes the copy step work. This is still a
strict improvement over the old design: only this one, deliberately
consenting account ever completes OAuth against it, instead of every admin.

Admins are invited as **Drive editors on the specific form**
(`drive.permissions.create(role='writer')`) instead of owning anything. That
triggers Google's native "shared with you" notification - there's no
in-app editing UI to build or maintain, admins just open the form in Google
Forms directly.

## Architecture

- `app/routers/submissions.py::get_google_credentials()` takes no arguments
  and builds credentials from `config.GOOGLE_REFRESH_TOKEN` - the one club
  token every Forms/Drive call in the backend uses. There is no per-event or
  per-admin token anymore.
- `POST /forms/{event_id}/attach` (`app/routers/forms.py`) is **idempotent**:
  if the event's form has no `google_form_id` yet, it copies the template
  (`config.TEMPLATE_FORM_FILE_ID`) within the club's own Drive, registers a
  Forms API watch (`config.GOOGLE_FORMS_TOPIC_NAME`) for the Pub/Sub sync
  pipeline, and records the responder URL. Either way - first attach or a
  later call with a different email - it shares the form with whatever
  `admin_google_email` the request carries. This is what powers "request
  access for a different email" in the UI: same endpoint, no re-copy.
- The request body's email is validated against `config.GOOGLE_ALLOWED_EMAIL_DOMAINS`
  (default `gmail.com,googlemail.com`) before any Drive call - Drive's API
  otherwise queues a pending share for a non-Google address with no error at
  all, which would look like success and never resolve.
- `POST /forms/{event_id}/unattach` deletes the Forms watch, revokes
  **every** admin's Drive permission (not just the most recent one - see
  below), and resets the form row (`form_type='registration'`, all
  `google_*`/`admin_google_email` columns cleared). The form file itself
  stays in the club's Drive; only access to it changes.
- `GET /forms/{form_id}/schema` is new: the old per-admin OAuth cookie session
  used to serve the responses page's column headers directly from the
  frontend. With that gone, the backend now exposes the form's schema
  (`fetch_schema`, unchanged) through an authenticated route instead.
- `PUT /events/{event_id}/status` (`app/routers/events.py`) also publishes or
  unpublishes the event's Google Form (`submissions.py::set_form_publish_state`,
  `forms.setPublishSettings`) whenever the transition crosses the "open"
  boundary, for events whose form is `form_type=google` with a `google_form_id`
  set. **This is not optional** - confirmed empirically that copying the
  template via `drive.files.copy` does not carry over "accepting responses";
  a freshly copied form shows members an unpublished-form page until
  something explicitly publishes it. The Forms API call happens before the
  DB write and is allowed to raise (no try/except) - a failed publish must
  not leave the event "open" while the form still silently rejects
  submissions, which is exactly the failure mode this exists to prevent.

## Multiple admins, one form

`forms.admin_google_email` is a single column - it only ever remembers the
most recently granted email. Early on this was also used to decide "does the
admin viewing this page currently have access", by comparing it against the
email this browser last saved locally. That breaks the moment a second admin
requests access: the column gets overwritten, and the first admin's
still-valid Drive permission now looks revoked to them - a false "request
access" prompt for access they never lost, confirmed by hitting it with two
real admin accounts on the same event.

The `form_access_grants` table (`form_id`, `google_email`, unique per pair)
fixes this by recording every grant, not just the latest. `Forms.granted_emails`
(a relationship-backed property, not a mapped column) exposes the full list
through `Form_model`; `POST /forms/{event_id}/attach` inserts into it
alongside the Drive `permissions.create` call, and `POST
/forms/{event_id}/unattach` revokes Drive access for everyone in it before
clearing it. `admin_google_email` still exists purely as a "last requested"
display convenience - it is no longer what "do I have access" is checked
against; the frontend now checks its saved email against `granted_emails`.

## What this is not

This does not touch Google's response-sync logic at all.
`extract_email_answer` and the email-shape matching in `sync_form_submissions`
(see [GOOGLE_FORMS_SYNC.md](GOOGLE_FORMS_SYNC.md)) are unrelated to
credentials and must not be touched by future changes here - that doc's fix
for unstable per-form question IDs still applies exactly as before.

## Hard cutover

This was a deliberate hard cutover: forms attached under the old per-admin
OAuth flow are not migrated. Any event still mid-flow when this shipped
simply gets re-attached through `POST /forms/{event_id}/attach` like any
other event.

## Env vars

See [ENVIRONMENT_VARIABLES.md](ENVIRONMENT_VARIABLES.md) for
`GOOGLE_REFRESH_TOKEN`, `TEMPLATE_FORM_FILE_ID`, `GOOGLE_FORMS_TOPIC_NAME`,
and `GOOGLE_ALLOWED_EMAIL_DOMAINS`.
