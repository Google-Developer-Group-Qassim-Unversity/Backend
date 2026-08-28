# Hardcoded action IDs

Three lists of `actions` primary keys are pasted into the source. They decide
which events an admin can create and whether those events can be attended, and
they have already drifted apart from each other.

```python
# app/routers/action.py:32-33  - which pairs the admin UI offers
department_ids = [51, 52, 53, 54, 86, 88, 90, 105]
member_ids     = [76, 77, 78, 79, 87, 89, 91, 108]

# app/DB/logs.py:55            - which events can be attended
ATTENDABLE_ACTION_IDS = [76, 77, 78, 79, 87, 89]
```

**The repository does not record what any of these rows are.** No migration
creates them, no seed script names them; they are rows someone inserted into
the production database, referenced by number. Before changing anything, dump
them - this is the only place the meaning exists:

```sql
SELECT id, action_name, ar_action_name, action_type, points, `order`, is_hidden
FROM actions
WHERE id IN (51,52,53,54,86,88,90,105, 76,77,78,79,87,89,91,108)
ORDER BY id;
```

## What the lists actually mean

`get_categorized_actions` zips the first two **positionally**, so the pairing is
carried by list order and nothing else:

| department | member | attendable? |
|---|---|---|
| 51 | 76 | yes |
| 52 | 77 | yes |
| 53 | 78 | yes |
| 54 | 79 | yes |
| 86 | 87 | yes |
| 88 | 89 | yes |
| 90 | 91 | **no** |
| 105 | 108 | **no** |

A "composite action" is not a row. `ActionsActionType.COMPOSITE` exists in the
enum and in three `Literal`s in `app/routers/models.py`, but nothing ever writes
it - a composite is only ever this zip, computed per request.

## What is broken because of it

**Two of the eight event types the admin UI offers cannot be attended.**
`ATTENDABLE_ACTION_IDS` is `member_ids` minus its last two entries. The `(90, 91)`
and `(105, 108)` pairs were added to `action.py` without `logs.py` being updated,
so an admin can create one of those events, members can register, and every
attendance call - QR scan, manual marking, backfill - fails:

```
500 {"detail": "Event has no attendable logs"}
```

`get_event_with_attendable_log` raises `DataIntegrityError` when the lookup
returns nothing, and every route in `app/routers/attendance.py` goes through it.
Confirm against prod before fixing, since the pairs may be unused in practice:

```sql
SELECT e.id, e.name, e.status, l.action_id
FROM events e JOIN logs l ON l.event_id = e.id
WHERE l.action_id IN (91, 108);
```

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
UPDATE actions SET is_attendable = 1 WHERE id IN (76,77,78,79,87,89);
-- and 91, 108 if the drift above is a bug rather than a decision
INSERT INTO composite_actions (department_action_id, member_action_id, `order`)
VALUES (51,76,0), (52,77,1), (53,78,2), (54,79,3),
       (86,87,4), (88,89,5), (90,91,6), (105,108,7);
```

Guard it: the rows may not all exist in every environment, so the migration
should insert only pairs where both actions are present, and log what it skipped
rather than failing the deploy.

### Checklist

- [ ] Dump the 16 rows from prod; write their names into the migration as comments
- [ ] Decide whether `(90, 91)` and `(105, 108)` should be attendable
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
