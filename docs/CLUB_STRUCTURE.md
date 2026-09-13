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

## Next steps

3. Add guarded API endpoints for overview, department settings, membership,
   leadership replacement, tenure history, and archive/restore. Admins view;
   super admins manage. Preserve the existing public department endpoints.
4. Add the page, navigation, two equal President slots, department cards,
   creation dialog, and Roster/Leadership/Settings drawer. Reuse the existing
   member picker and request/query components. Hide Leadership for the Board.
5. Add bilingual messages, RTL and mobile layouts, theme support, meaningful
   loading/error/empty states, and cache refreshes. Count distinct people.
6. Validate API permissions, transactions and concurrent replacements, archived
   departments, and frontend flows against the supplied Figma source.

## Archive scope and rollout

Version one uses the existing active/archived status and retains roster and
points records when archiving. It does not promise historical leaderboard
visibility: existing leaderboard queries filter the current active status even
when requesting a past semester. Date-aware visibility across repeated
archive/restore cycles requires a separate status-history table and query work.

Steps 1 and 2 run migrations only against isolated test databases. They do not
apply them to a shared development or production database. A later rollout
applies the migration before deploying the new backend. Downgrading drops the
assignment table and its tenure history; the corrected Board name is retained
because the previous English name cannot be reconstructed. Other existing
department data and references are preserved.
