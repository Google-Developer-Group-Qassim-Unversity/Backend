# Club structure

Who is in which department, with which role, **per semester**. Replaced the
`club_assignments` tenure log in migration `e2f3a4b5c6d7`; the design and the
reasons are on Notion (GDG → Improvements → Idiot decisions of our app → 2 ·
Club structure).

## Tables

| Table | What a row is |
| --- | --- |
| `club_roles` | A role: `leader`, `vp`, `member`, with `name` / `ar_name` and a default seat limit `max_holders` per department per semester (NULL = unlimited) |
| `department_role_limits` | One department's own limit for a role. Seeded: Leadership · leader · 2 |
| `semester_departments` | A department that existed in a semester, with the `name` / `ar_name` it had then (NULL = today's name) |
| `club_memberships` | One role one person holds in one department for one semester |
| `club_membership_changes` | Append-only: who added or removed which role, and when |

`club_memberships` has a composite foreign key to `semester_departments`, so
nobody can be put in a department that was not part of that semester, and
`member_id` is a foreign key to `members` - the only source of people. Nothing
is hardcoded and nobody without a `members` row appears anywhere.

Departments carry two flags:

- `show_in_leaderboard` - 0 leaves the department out of the department ranking
  (the Board, Leadership). There is no name check anywhere.
- `is_club_leadership` - the one department whose leaders are the club's
  presidents. A unique generated column allows at most one.

`active` (archive/restore) now only means "offered for new semesters": an
archived department cannot be added to a semester and is skipped when a
semester is copied. Past semesters, their rosters and points are untouched.

## Rules (enforced in `app/services/club_structure.py`)

- **Leaders and VPs are members too, in their own row.** Granting a role adds the
  person's `member` row if it is missing. Removing someone from a department
  removes every role they hold there. Revoking leader/VP leaves them a member.
- **Seat limits** come from `department_role_limits`, else `club_roles`. They are
  counted under a `SELECT … FOR UPDATE` on the `semester_departments` row, and
  the roster reads are locking reads too, so a REPEATABLE READ snapshot cannot
  hide a concurrent grant. `tests/test_club_structure_concurrency.py` races real
  connections to prove it.
- **Replacing** a holder names them (`replaces_member_id`). A full seat without
  it, or naming someone who no longer holds the role, is a 409 - a stale screen
  cannot take a seat from someone it did not show.
- **Removing a department from a semester** is refused while it has a roster or
  earned points in that semester.
- **Copying** (`POST /club-structure/semesters/{id}/copy-from/{source}`) only
  works into a semester with an empty roster, skips archived departments and
  uses today's names.
- **Deleting a semester** is refused while it has a roster; its empty department
  list and change log go with it (`ON DELETE CASCADE`).

Every mutation runs in a savepoint and every change is logged with the Clerk
subject of the super admin who made it. Club roles never grant app access.

## API

Admin reads take `?semester_id=<uuid>` and default to the current semester
(the calendar rule in `app/semesters.py`). Writes name the semester in the path.

| Route | Guard |
| --- | --- |
| `GET /club-structure` - overview: departments with role seats, available departments, roles, distinct people | admin |
| `GET /club-structure/roles` | admin |
| `GET /club-structure/departments/{id}` / `…/roster?semester_id=` | admin |
| `GET /club-structure/history?semester_id=&department_id=&member_id=` | admin |
| `POST /club-structure/departments?semester_id=` (joins that semester), `PUT …/{id}`, `POST …/{id}/archive`, `…/restore` | super admin |
| `POST` / `DELETE /club-structure/semesters/{sid}/departments/{did}` | super admin |
| `POST …/{sid}/departments/{did}/members`, `DELETE …/members/{mid}` | super admin |
| `PUT` / `DELETE …/members/{mid}/roles/{role}` (leader, vp) | super admin |
| `POST /club-structure/semesters/{sid}/copy-from/{source}` | super admin |
| `GET /club-structure/public?semester=<hijri code>` | public |

The public payload keeps the shape the leaderboard app reads: `presidents` (the
Leadership department's leaders, which is not listed as a department),
per-department `leader`, `deputy` (the VP) and `members` (everyone else), plus
`semester` and `show_in_leaderboard`. `leadership_enabled` is kept for that app
and is now just "has a leader or VP this semester".

## Department ranking

`/points/departments/total?semester=` lists the departments that are part of the
semester **or** earned points in it (so points are never hidden by a missing
row), leaves out `show_in_leaderboard = 0`, and uses the semester's name for a
department when one is recorded.

## Migration `e2f3a4b5c6d7`

Audits first and aborts before any DDL. Then: adds the department flags (the
Board, found once by its name `Board of Directors`, gets `show_in_leaderboard =
0`), creates the Leadership department, the five tables, and seeds
`semester_departments` with every department that earned points in each
semester plus every active department in the current one. The open
`club_assignments` rows become the current semester's roster (deputy → vp,
presidents → Leadership leaders, plus explicit member rows); closed rows are
discarded and the table is dropped. `downgrade()` recreates it empty - restoring
its rows means restoring a backup.

Verified on a production copy (2026-09-27): 68 open assignments became 86
roster rows exactly as mapped; no department's points changed. Past rankings
now show the archived departments that earned points then (e.g. Cybersecurity
in 471) and no longer list departments that did not exist yet.

## Importing past semesters

`scripts/import_club_structure.py` loads past structures from CSV, one row per
role held (format and rules in `app/services/club_import.py`):

```csv
semester,department,member,role,department_name,department_ar_name
461,Development,441234567,leader,,
461,التصميم,sara@qu.edu.sa,member,,
461,7,#1214,vp,Innovation,قسم الابتكار
```

- `semester` is the Hijri code and must exist already (Settings → Semesters).
- `department` is an id or its current English/Arabic name. A department that
  is gone today is created (and archived) in the admin app first.
- `member` is a university id, an email or `#<members.id>`. Nobody is created;
  an unknown person fails the import.
- `role` is `leader`, `vp` or `member`. Leaders and VPs get their member row.
- The two name columns record what the department was called that semester.

It is a dry run unless `--apply` is passed. Every row in every file is checked
first and all problems are listed together; any problem, including a full
seat, means nothing is written. Rows already present are skipped, so it can be
re-run. Against production:

```bash
infisical run --env=prod --path=/admin-backend -- uv run python scripts/import_club_structure.py 461.csv 462.csv
```

## Tests

- `tests/routers/test_club_structure.py` - every route through the real guards
- `tests/test_club_structure_concurrency.py` - independent MySQL transactions
- `tests/routers/test_points.py` - the department ranking rules
- `tests/test_club_import.py` - the CSV import and the script
- `tests/test_club_structure_browser.py` + `Frontend/tests/club-structure/` -
  Chromium against the real API (`RUN_CLUB_BROWSER=1`, runs in CI)
