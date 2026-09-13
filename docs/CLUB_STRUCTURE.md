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

## Next steps

2. Implement queries and transactional services: lock the department during
   membership and leadership changes; check the expected assignment during
   replacements; close former periods before opening new ones. Enforce the
   Board setting and reject changes to archived rosters. President writes must
   handle occupied-slot conflicts without assigning a third person. Translate
   database conflicts into useful HTTP responses.
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

This step only writes and tests the migration. It does not apply it to a shared
development or production database. A later rollout applies it before deploying
the new backend. Downgrading drops the assignment table and its tenure history;
the corrected Board name is retained because the previous English name cannot
be reconstructed. Other existing department data and references are preserved.
