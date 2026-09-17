# Club Structure implementation

Work takes place on `feat/club-structure`, in small commits. Stage only files
changed for this feature, commit each validated step, and push the feature
branch. Do not commit to or merge into `main`.

## Step 1: database foundation

The migration `8c9211f55b8b` extends the existing `departments` table and creates
one table, `club_assignments`. Existing members, departments, and points remain
the source of truth. No memberships are inferred from event or points records.

Departments gain `color`, `icon`, `leadership_enabled`, `created_at`, and
`updated_at`. Existing creation dates remain unknown (`NULL`); new departments
get a creation timestamp. The existing board department is renamed to **Board
of Directors** in its English name field, retaining its ID and Arabic label.
The migration recognizes both Arabic spellings previously used for the board
and disables its department leadership controls. Subsequent behavior must use
`leadership_enabled`, not department-name comparisons.

Assignments store `member_id`, optional `department_id`, `role`, `starts_at`,
`ends_at`, and the assigning Clerk subject in `changed_by`. `ended_by` records
the actor who closes a tenure. Timestamps use UTC, with microsecond precision.
Database connections must use UTC when relying on the insert timestamp default.

| Role | Department | Current seats |
| --- | --- | --- |
| `president` | None; applies to the whole club | Two equal slots, numbered 1 and 2 for uniqueness only |
| `leader` | Required; leadership enabled | One per department |
| `deputy` | Required; leadership enabled | One per department |
| `member` | Required | Multiple people |

A President may independently hold a Board membership. A member has at most
one current role within each department. Department leaders are included in
the roster through that role; they do not need duplicate `member` rows.
Assignments do not create or change rows in the application permission table.

The database checks role scope, President slot bounds, and chronological
periods. Generated keys enforce current-member, leader/deputy, and President
slot uniqueness, while closed periods retain unlimited history. This uses
[MySQL generated column indexes](https://dev.mysql.com/doc/refman/8.0/en/generated-column-index-optimizations.html).
Foreign keys restrict deleting referenced members and departments, including
those with only past assignments, so deletion cannot silently erase tenure.

Leadership-enabled and archive rules span tables and belong in the service
layer in Step 2. They are not yet enforced by an API in this database-only step.

## Step 2: queries and transactional services

`app/DB/club_assignments.py` reads current rosters, the two President seats,
filtered/paginated tenure history, department counts, and distinct club people.
Leaders and deputies count as roster members. The club count includes Presidents
and people in active departments, counting each person once; `include_archived`
includes archived rosters. Individual rosters and history remain readable when
archived. Empty departments have a count of zero.

`app/services/club_structure.py` creates and edits departments, archives/restores
them, adds/removes roster members, and fills/replaces/clears leadership and
President seats. Callers supply the authenticated Clerk subject as `changed_by`
and commit the session before sending a successful response. These services use
[SQLAlchemy savepoints](https://docs.sqlalchemy.org/en/20/orm/session_transaction.html#using-savepoint)
to roll back a failed replacement's closures and inserts together. They do not
commit the caller's outer transaction. Deadlocks/lock timeouts require rollback
of the outer transaction and a fresh request.

Department roster changes and archive/restore lock the same department row.
Assignment checks use
[MySQL locking reads](https://dev.mysql.com/doc/refman/8.0/en/innodb-locking-reads.html)
and refresh cached ORM objects, so a transaction cannot change an archived
roster or replace a newer holder using an old snapshot. `leadership_enabled` is
the sole capability check, and ordinary department settings cannot change it.
Names, Arabic labels, type, color, and icon can be edited, including while
archived; assignments and points are retained through archive/restore.

Replacement calls require `expected_assignment_id`; `NULL` means the caller
saw an empty seat. A mismatched ID returns a conflict. Removing a roster member
also requires the current assignment ID. A leader/deputy replacement closes the
old leader's period and any incoming regular membership, then opens the new
leader's period and a regular membership for the former leader, all at one UTC
timestamp. Clearing a leadership seat also retains regular membership. Removing
someone from the roster ends their department role entirely. Moving between
leader and deputy requires explicitly clearing the other seat first.

President replacements lock the exact expected tenure. Claims on empty seats
use the existing unique indexes to arbitrate concurrent inserts, avoiding locks
on empty index ranges. The two slots remain equal, and a President change never
changes Board membership. Re-selecting the current holder with the correct
expected ID is a no-op and does not create another tenure.

Known assignment conflicts return HTTP 409 with a useful message through the
existing exception handler. Missing records return 404 and invalid service
inputs return 422. Application, Alembic, and test connections explicitly set
the MySQL session timezone to `+00:00`; new tenure values use UTC with microsecond
precision. No additional migration or API endpoints are introduced in Step 2.

MySQL tests cover complete rollback after failed replacements, concurrent
membership/leadership/President writes, stale reads, archive locking, UTC insert
defaults, distinct counts, and tenure preservation. Concurrency tests use
independent connections and clean up only their own committed fixture records.

## Step 3: guarded API endpoints

The management API is under `/club-structure`. Reads use the existing
`admin_guard` (admins, points admins, and super admins); every write requires
`super_admin_guard`. The public `/departments` endpoints retain their existing
authorization and four-field payload (`id`, `name`, `ar_name`, `type`).

| Method | Path under `/club-structure` | Result |
| --- | --- | --- |
| GET | empty path | Department cards, two equal President slots, distinct `total_members` |
| GET | `/departments/{department_id}` | Department settings and capability/status fields |
| GET | `/departments/{department_id}/roster` | Current assignments, including leaders/deputies |
| GET | `/history` | Paginated current and closed tenures |
| POST | `/departments` | Create a department; returns 201 |
| PUT | `/departments/{department_id}` | Replace all editable settings |
| POST | `/departments/{department_id}/archive` | Archive, retaining roster and points |
| POST | `/departments/{department_id}/restore` | Restore the existing roster |
| POST | `/departments/{department_id}/members` | Add a regular member; returns 201 |
| POST | `/departments/{department_id}/members/batch` | Add multiple regular members atomically; returns 201 |
| DELETE | `/departments/{department_id}/members/{member_id}` | Close the exact expected department tenure |
| PUT | `/departments/{department_id}/leadership/{role}` | Fill, replace, or clear `leader`/`deputy` |
| PUT | `/presidents/{slot}` | Fill, replace, or clear slot 1 or 2 |

Overview defaults to active departments; `include_archived=true` includes
archived cards and people. Both President slots are always present, with a
nullable `assignment`. Cards include `member_count`, `leader`, and `deputy`.
The new read query loads leadership in bulk rather than querying each card.

Assignment responses include the tenure ID, member/department IDs, role,
President slot, UTC timestamps with a `Z` suffix, actor subjects, and a minimal
`member` object containing only `id` and `name`. They do not expose generated
database keys or additional member contact/profile fields. Existing unknown
department creation timestamps remain `null`.

Creation accepts `name`, `ar_name`, `type`, and optional `color`/`icon`. Updating
requires all five editable fields so omitted appearance settings cannot silently
reset to defaults. IDs, timestamps, `active`, and `leadership_enabled` are not
editable through settings. Department creation/settings/archive/restore refresh
the existing public leaderboard cache after a successful commit. Cache failures
are logged without misreporting a committed change as a failed mutation.

Membership creation accepts `{"member_id": 123}`. Atomic batch creation accepts
`{"member_ids": [123, 456]}` with 1–100 unique IDs. Leadership and President
writes require both `member_id` and `expected_assignment_id`, even when either
value is `null`:

```json
{"member_id": 123, "expected_assignment_id": null}
```

This example claims a vacant seat. Supply the current tenure ID to replace it;
set `member_id` to `null` to clear it. Successful clears return JSON `null`.
Removing a roster member requires the `expected_assignment_id` query parameter
and returns the closed tenure. Body IDs must be positive integers; unknown
fields, including forged actor IDs or timestamps, are rejected.

Assignment actors come only from the authenticated super admin's Clerk `sub`.
The caller need not have a member row. Assignments never create members or
change permission rows. Missing/invalid subjects fail before any assignment
write. Service conflicts remain HTTP 409, missing records return 404, and
invalid payloads return 422. Responses are validated and writes committed
before returning success.

History accepts optional `department_id`, `member_id`, and `role` filters,
`limit` from 1 to 100 (default 50), and nonnegative `offset`. It returns `items`,
`limit`, `offset`, and `has_more`; archived tenure stays readable. Every route
has an explicit response model and is recorded in the auth inventory. HTTP
tests run the real guards with only JWT verification stubbed, and cover actor
spoofing, stale replacements, rollback on commit failure, archive protection,
history pagination, UTC serialization, and public API compatibility.

## Step 4: management page and department drawer

`Frontend/app/club-structure/page.tsx` adds the management page, with links in
the navigation and dashboard. It follows the supplied
[Figma Make reference](https://www.figma.com/make/usYqch40Q7dRmRsuTdvTHi/Design-Club-Structure-Page)
and its React source: four overview statistics, active/archive filters,
department search, department cards, a creation dialog, and a 480px department
drawer. Two equal President seats are added above the departments, as required
by the implementation plan. Existing app components and theme tokens supply
the controls and colors. The reference's **Specialized** label maps to the
existing `practical` API enum; it does not introduce a new department type.

The drawer contains Roster, Leadership, and Settings tabs. The Board's
Leadership tab and card leadership rows are hidden using `leadership_enabled`.
The roster shows names and roles from the management API, which does not
expose member emails. Super admins can add/remove members, assign/replace/clear
both leadership roles and President seats, edit the five department settings,
and archive/restore departments. Admins and points admins can read these views;
editing controls are omitted and settings are disabled. Archived rosters remain
readable; their assignment controls become available again after restoration.

The existing `MemberSelectDialog` gains optional single-selection, loading,
error, and existing-members-only modes. Existing points callers retain their
multiple-selection/create behavior. Roster additions can select multiple
existing members at once; leadership and President assignments remain
single-selection. These flows never create an account or alter application
permissions.
The shared `useMembers()` query now owns its token through `useApi()`; older
standalone member calls and member creation remain on the legacy API adapter.

`lib/api/resources.ts` owns the typed `/club-structure` resource. Query hooks
refresh overview, roster, department, and existing event department selectors
after writes. Seat changes capture `expected_assignment_id` when the picker
opens, so a later query refresh cannot silently approve replacing a newer
holder. Removal sends the exact roster tenure ID. Conflicts refresh the views
and require closing/reviewing the confirmation before trying again; mutations
are not automatically retried.

The headline member count comes from the API's distinct active-club count,
including Presidents. An inclusive overview supplies archive cards and
department counts. Searching/filtering cards does not change the headline
count. Archive copy reflects the existing current-status leaderboard filtering;
it does not promise date-aware historical visibility.

English/Arabic messages, responsive grids, RTL drawer placement, theme tokens,
and basic loading/error/empty states are included as foundations for the next
step, keeping these working controls consistent with the rest of the app.

Validation: frontend typecheck and production build pass (the build uses
Infisical's development frontend settings); ESLint reports zero errors and 18
existing warnings. Isolated Chromium checks render the actual page/components
with mocked Clerk metadata and API responses. They cover both read-only admin
roles, Board leadership visibility, distinct counts, seat and roster changes,
expected-tenure payloads, conflict recovery, settings, archive/restore, creation,
Arabic mobile layout, and query error/empty states. These are component-flow
checks, not a deployed frontend/backend or live Clerk integration test.

Backend Ruff format/check pass and the isolated MySQL suite reports 771 passed,
48 skipped, and one expected failure. The documented `mypy` command is unavailable
in the current dependencies; Pyright also reports environment/import-resolution
errors. Step 4 changes backend documentation only, not Python or migrations.

## Step 5: bilingual UI, responsive states, and refresh behavior

English and Arabic copy now covers refresh, stale data, settings conflicts,
validation, and unavailable member choices. API errors use localized messages
based on HTTP status rather than exposing untranslated backend prose. An
unconfirmed write tells the user to refresh and check whether it was saved.
Counts use locale-aware formatting, including Arabic plurals. Close buttons,
search fields, and form controls have translated accessible labels.

Cards and rosters wrap long names and isolate mixed-direction text. Dialogs use
dynamic viewport heights, the member picker keeps its actions outside scrolling
lists, and mobile controls have larger touch targets. The drawer uses logical
spacing and RTL-aware tabs. Badges and selected colors work in light/dark themes.
Loading skeletons announce their status; search empty states offer a clear-search
action, and member selection distinguishes no eligible members from no matches.
The shared points picker retains multiple selection and member creation.

Club queries become stale after 30 seconds and refresh on window focus or
reconnection. Explicit Refresh controls update active club queries and invalidate
inactive ones. Every settled mutation refreshes club data and existing event
department selectors, including when the response was lost or returned an error.
Pending reads are cancelled before invalidation so a pre-write response cannot
repopulate the cache. Missing-member and assignment conflicts also invalidate
member choices. Writes are never automatically retried; reads retry transient
failures once and do not retry 4xx responses.

Background query failures retain the last loaded data with an explanation and
disable changes that depend on the failed query. Confirmation dialogs disable
repeat submission after an error and preserve the original assignment expectation.
Archive/restore confirmations retain the intended status across refreshes.
Settings drafts survive tab switches and refreshes; a changed server snapshot
blocks saving until the user loads the latest settings or resets the draft.
This detects observed changes, not server-side optimistic locking for settings.

Validation: frontend typecheck and production build pass with development
settings from Infisical. ESLint has zero errors and 18 existing warnings.
Isolated Chromium checks exercise the actual components with mocked auth/API
responses, including assignment flows, read-only roles, stale-data recovery,
settings drafts, and 320px English/Arabic layouts in light/dark themes. These
checks do not replace the integrated validation in Step 6.
Backend Ruff format/check pass; the isolated MySQL suite reports 771 passed,
48 skipped, and one expected failure. `mypy` remains unavailable in the existing
dependencies. This step changes backend documentation only.

## Step 6: integration and regression validation

`tests/test_club_structure_browser.py` starts a local API server with independent
request sessions against the migrated MySQL test database. The checked-in
`Frontend/tests/club-structure/` runner bundles the actual page, controls, API
client, query provider, translations, and fresh Tailwind CSS. Chromium sends real
HTTP requests. Only Clerk verification/browser metadata and the external
leaderboard cache call are stubbed; permission guards and club data operations
remain unchanged. Test records are uniquely scoped and cleaned up on failure.

| Requirement | Validation |
| --- | --- |
| API permissions | Route inventory and full read/write role matrix; browser read-only controls plus direct forbidden requests |
| Atomic replacements | Rollback and constraint tests; independent MySQL sessions compete for vacant/occupied seats and exercise deadlock recovery |
| Stale clients | An open browser picker retains its original expected tenure while another HTTP caller replaces it; confirmation returns 409 and refreshes the winner |
| President/Board independence | Browser membership and President changes retain the Board roster; competing HTTP replacements leave one current holder and intact history |
| Archive/restore | Two cycles preserve exact roster/tenure records, block assignments, and restore active views |
| Existing points | A published event earns department points; two archive/restore cycles retain totals, event history, and club tenure |
| Frontend design | Supplied Figma React source compared with overview statistics, search/status filters, cards, creation, roster/leadership/settings, and the 480px drawer; English/Arabic mobile checks in both themes |

The planned differences from the reference remain explicit: two equal President
seats, distinct-person counts, Board behavior controlled by capability, existing
member selection, app theme/navigation, and the documented current-status archive
visibility. The reference's future-only archive wording does not match the
existing leaderboard and is not promised by this implementation.

Run `RUN_CLUB_BROWSER=1 uv run pytest` from `Backend/` after installing frontend
dependencies and Playwright Chromium. Set `CHROMIUM_PATH` to use a system browser.
Leave `DATABASE_URL` unset to use the isolated MySQL container. See
`Frontend/tests/club-structure/README.md` for setup and screenshot locations.
The new `club-structure-integration.yml` workflow runs the suite on PRs and saves
browser screenshots. Ordinary backend runs skip the opt-in browser test.

Final local validation with the browser enabled: **773 passed, 48 skipped,
1 expected failure**, with two dependency deprecation warnings. Ruff format/check,
frontend typecheck, and the production build pass. ESLint reports zero errors
and the existing 18 warnings. `mypy` remains absent from the backend environment.
Workflow YAML parses locally; the GitHub Actions job is configured for the PR.

This validates the component/API/database integration. Live Clerk sign-in,
production deployment, and shared-database migration remain rollout work.

## Archive scope and rollout

Version one uses the existing active/archived status and retains roster and
points records when archiving. It does not promise historical leaderboard
visibility: existing leaderboard queries filter the current active status even
when requesting a past semester. Date-aware visibility across repeated
archive/restore cycles requires a separate status-history table and query work.

Steps 1 through 3 run migrations only against isolated test databases. They do not
apply them to a shared development or production database. A later rollout
applies the migration before deploying the new backend. Downgrading drops the
assignment table and its tenure history; the corrected Board name is retained
because the previous English name cannot be reconstructed. Other existing
department data and references are preserved.
