# Hardcoded action IDs

Three lists of `actions` primary keys are pasted into the source. They decide
which events an admin can create and whether those events can be attended.
They disagree with each other, and nothing in the code says whether that is
deliberate - you have to query production to find out (below).

```python
# app/routers/action.py:32-33  - which pairs the admin UI offers
department_ids = [51, 52, 53, 54, 86, 88, 90, 105]
member_ids     = [76, 77, 78, 79, 87, 89, 91, 108]

# app/DB/logs.py:55            - which events can be attended
ATTENDABLE_ACTION_IDS = [76, 77, 78, 79, 87, 89]
```

**The repository does not record what any of these rows are.** No migration
creates them, no seed script names them; they are rows someone inserted into
the production database, referenced by number. Dumped from prod on 2026-09-03,
they are:

| dept | department action | member | member action | pts | attendable |
|---|---|---|---|---|---|
| 51 | Organized an On-site course | 76 | On-site course attendance | 6 | yes |
| 52 | Organized an Online course | 77 | Online course attendance | 4 | yes |
| 53 | Organized a Bootcamp | 78 | Bootcamp attendance | 10 | yes |
| 54 | Organized a Technical meetup / Monthly session | 79 | Meetup or Monthly session attendance | 3 | yes |
| 86 | Organized an Online Bootcamp | 87 | Online Bootcamp attendance | 6 | yes |
| 88 | Organized a massive event (300+) | 89 | massive event attendance | 15 | yes |
| 90 | Organized a twitter space | 91 | attend a twitter space | **0** | no |
| 105 | Host a Tournament | 108 | مشاركة في بطولة | 5 | no |

Re-run this if you suspect it has drifted again:

```sql
SELECT id, action_name, ar_action_name, action_type, points, `order`, is_hidden
FROM actions
WHERE id IN (51,52,53,54,86,88,90,105, 76,77,78,79,87,89,91,108)
ORDER BY id;
```

Note that `action_type` does not line up with the lists at all: 51-54 and 86-91
are typed `composite`, 76-79 are `member`, 105 is `department`, and 108 is
`bonus`. Attendability cannot be derived from that column - it has to be its
own flag.

## What is broken because of it

**Two of the eight pairs cannot be attended - probably on purpose.**
`ATTENDABLE_ACTION_IDS` is `member_ids` minus its last two entries, so an event
built on the `(90, 91)` or `(105, 108)` pair 500s on every attendance route:

```
500 {"detail": "Event has no attendable logs"}
```

`get_event_with_attendable_log` raises `DataIntegrityError` when the lookup
returns nothing, and every route in `app/routers/attendance.py` goes through it.

Checked against prod (2026-09-03): eight events use those two pairs - five
twitter spaces and three tournaments - and **none of them has a single
attendance row**. That is not evidence of loss so much as evidence the two
pairs are for event types where attendance is not a thing. A twitter space has
no attendance list, and `attend a twitter space` is worth 0 points. Tournaments
award participation to *departments* (`departments_logs`), and individual
placings through separate bonus actions, neither of which touches the
attendance path.

So the exclusion looks deliberate rather than dropped, even though nothing in
the code says so. The one loose end is event 350 (بطولة الشطرنج), which has 5
form submissions and no member awards at all - worth a look, but it is one
event and there are other explanations.

**Whichever way that goes, the flag has to carry it.** The current arrangement
encodes "not attendable" as absence from a list in a different file from the
one that defines the pairs, which is indistinguishable from someone having
forgotten to update it - which is exactly how it reads until you query the
database.

**A new action can never be attendable.** `create_action` takes the next
autoincrement ID, so anything an admin creates through `POST /actions` falls
outside all three lists. New action types therefore need a code change and a
deploy, which defeats the point of having them be data.

**Deleting an action silently empties a slot.** `DELETE /actions/{id}` has no
idea these lists exist. Delete one half of a pair and `get_categorized_actions`
drops it - the `if dept_action is not None and member_action is not None` guard
means the pair just stops appearing in the admin UI, with no error anywhere.

**Any database that is not production is wrong by construction.** A restored
backup, a staging DB, a fresh local DB, or the test containers assign different
IDs. That is why `tests/routers/test_attendance.py` replaces
`log_queries.get_attendable_logs` for the whole module: the real function cannot
return anything against seeded data. The production lookup therefore has *no*
coverage. `tests/journeys/test_member_lifecycle.py` takes the other route and
seeds an action *at* ID 76 so the real function runs, which is why
`ATTENDABLE_ACTION_ID` exists there.

## The fix

Both concepts belong in the `actions` table, which already carries presentation
metadata (`order`, `is_hidden`), so there is precedent and no new pattern.

1. **`actions.is_attendable`** (`TINYINT(1) NOT NULL DEFAULT 0`) replaces
   `ATTENDABLE_ACTION_IDS`.
2. **A `composite_actions` table** (`department_action_id`, `member_action_id`,
   plus `order`) replaces the positional zip. A join table rather than a
   self-referential `pair_id` because the pairing is a thing in its own right -
   it is what the admin event form actually selects - and it can then carry its
   own ordering instead of borrowing the list's.

The literals above *are* the backfill, so the migration is mechanical:

```sql
-- 91 and 108 stay 0: spaces and tournaments do not mark attendance (see above)
UPDATE actions SET is_attendable = 1 WHERE id IN (76,77,78,79,87,89);
INSERT INTO composite_actions (department_action_id, member_action_id, `order`)
VALUES (51,76,0), (52,77,1), (53,78,2), (54,79,3),
       (86,87,4), (88,89,5), (90,91,6), (105,108,7);
```

Guard it: the rows may not all exist in every environment, so the migration
should insert only pairs where both actions are present, and log what it skipped
rather than failing the deploy.

### Checklist

- [x] Dump the 16 rows from prod (table above; copy the names into the migration)
- [ ] Confirm `(90, 91)` and `(105, 108)` stay non-attendable - the prod data says yes
- [ ] Look at event 350 (بطولة الشطرنج): 5 submissions, zero member awards
- [ ] Alembic revision: `is_attendable` column + `composite_actions` table + backfill
- [ ] `get_attendable_logs` joins `actions` on `is_attendable = 1`
- [ ] `get_categorized_actions` reads `composite_actions` instead of zipping
- [ ] `create_action` / `update_action` accept and return `is_attendable`
- [ ] Admin UI: expose the flag, and manage pairs instead of assuming eight
- [ ] Delete the module-scoped `patch_get_attendable_logs` fixture in
      `tests/routers/test_attendance.py` - the real lookup can run now
- [ ] Delete `ATTENDABLE_ACTION_ID` from `tests/journeys/test_member_lifecycle.py`
      and set `is_attendable` on the seeded member action in `conftest.py`
- [ ] `DELETE /actions/{id}` refuses, or repoints, an action a composite pair uses

The last two test items are the signal that the fix landed: attendance is
currently the one flow the suite cannot exercise without patching the code
under test.
