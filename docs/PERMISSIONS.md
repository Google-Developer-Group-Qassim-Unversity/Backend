# Permissions

Who can do what in the admin app. Clerk only says **who** someone is; our
database says **what they can do**. Nothing reads Clerk metadata for access.

## The model

| Who | Gets |
| --- | --- |
| A regular user (not on this semester's roster) | nothing here - gdg.com only |
| Anyone on the **current semester's roster** (`club_memberships`) | **staff**: the admin app, and the staff basics (`admin.access`, `events.view`, `club_structure.view`, `uploads`) |
| A **leader or VP** of department D | for D: the **shared** permissions + **D's own** permissions, + its **team's** permissions if D is the pipeline's Design, Logistics or Media (found by name) |
| A plain **member** of D | for D: what a leader or VP of D **granted** them this semester |
| A **super admin** (`super_admins`) | everything, everywhere, roster or not |

The current semester is the latest one that has started (Riyadh time,
`app/semesters.py`). On a new semester's start date its roster takes over, and
last semester's grants stop counting.

## Tables

| Table | One row means |
| --- | --- |
| `super_admins` | this member is a super admin |
| `shared_permissions` | every leader and VP has this permission |
| `department_permissions` | this department's leaders and VPs have this permission |
| `permission_grants` | a leader/VP gave this member this permission, for this department, this semester (revoking keeps the row) |

A permission is a key string. The keys, their scope and labels live in code:
`app/services/permissions/catalogue.py` (`Perm`, `CATALOGUE`). A key in the
database that is not in `Perm` is ignored.

**Scope.** A `club` permission applies everywhere; holding it through any
department is enough. A `dept` permission applies only to things belonging to
the department it is held for - an AI leader edits AI's events, not Robotics'.

## The code

- `app/services/permissions/access.py` - `resolve_access(session, member) -> Access`,
  once per request. `Access.can(perm, department_id=None)`, `can_any(perm, departments)`,
  `require(...)`, `departments_for(perm)`, `permissions()`, `is_staff`, `is_super_admin`.
- `app/services/permissions/dependencies.py` - `CurrentAccess` (the caller's
  `Access`) and `CurrentCaller` (member + access).
- `app/services/permissions/guards.py` - route guards:
  - `Staff` = `Require(Perm.ADMIN_ACCESS)`
  - `Require(Perm.X)` - held anywhere
  - `Require(Perm.X, event_departments | form_departments | path_departments)` -
    held for the department of the thing in the path. The permission is checked
    "anywhere" first, so a regular user never learns whether an id exists.
  - `SuperAdmin`
- `app/services/permissions/departments.py` - the resolvers above.
- `app/services/permissions/management.py` - the rules for changing who holds what
  (grants, assignments, super admins); used by `app/routers/permissions.py`.
- `GET /access/me` and `GET /access/events/{id}` - what the caller can do, for
  the frontends.

Frontend: `middleware.ts` lets in staff only (signed 5-minute `gdg_access`
cookie over `/access/me`); `lib/access.ts` mirrors the keys and each page's
permission; `useAccess()` in pages and components.

## How to

**Protect a new route.** Add a guard and pin it in `tests/test_route_auth.py`:

```python
@router.put("/{event_id:int}/thing", response_model=..., dependencies=[Depends(Require(Perm.EVENTS_EDIT, event_departments))])
```

If the department is in the body, take `access: CurrentAccess` and call
`access.require(Perm.X, body.department_id)` in the handler.

**Add a permission.** Add it to `Perm` and `CATALOGUE` (scope, English and
Arabic label), and to `PERMS` / `DEPARTMENT_SCOPED` / `PERM_GROUPS` (the
section it shows under on `/permissions`) in `Frontend/lib/access.ts`.
Nobody holds it until a super admin assigns it on `/permissions` (or a
migration seeds it).

**Make someone a super admin on a fresh database.**
`uv run python scripts/add_super_admin.py --uni-id <id> --apply`. After that,
super admins add each other on `/permissions`.

**Test.** Route tests that are not about permissions use `admin_client` /
`super_admin_client`, which stand in an `Access` double (`tests/access_doubles.py`).
Tests about permissions build real rosters with the `club` fixture
(`tests/access_support.py`) - see `tests/routers/test_access.py`.
