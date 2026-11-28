# Points Model: First-Class Bonuses & Rollout

## Overview

Bonuses and custom points are now modeled as first-class entities separate from `events`, replacing the legacy pattern of creating fake `events` rows (`location_type IN ('none', 'hidden')`) with dummy `Bonus` / `Discount` actions and `modifications`.

Refactor 2 (inlining `actions` into `event_actions` / points versioning for real events) is deliberately kept separate so this change ships cleanly without touching attendance or event creation flows.

---

## Schema (`alembic/versions/a7b8c9d0e1f2_separate_bonuses_from_events.py`)

All new tables use time-ordered UUIDv7 primary keys (`UUID_CHAR`, `new_id`).

### `bonuses`
- `id` (`CHAR(36)` PK)
- `title` (`VARCHAR(150)` NOT NULL)
- `event_id` (`INT UNSIGNED`, nullable FK -> `events.id` `ON DELETE CASCADE`)
  - `NULL` for standalone bonuses (replaces `location_type IN ('none', 'hidden')` fake events).
  - Set for event-attached custom bonuses/discounts (`/points/full`) and pipeline late-fix penalties.
- `semester_id` (`CHAR(36)`, FK -> `semesters.id` `ON DELETE RESTRICT`, NOT NULL)
  - Resolved from `given_at` at save time (or inherited from `events.semester_id` when `event_id` is set). Editing a semester's dates never moves existing bonuses.
- `legacy_id`, `legacy_status`, `legacy_end_datetime` preserve the old iOS event identity, point eligibility and event dates. New bonus IDs in the compatibility API start above the legacy unsigned INT range.
- `given_at` (`DATETIME` NOT NULL)
- `is_public` (`TINYINT(1)` NOT NULL, default `1`) — hidden bonuses (`0`) still count in totals and are omitted from public history & `/events/me`.
- `created_by` (`INT UNSIGNED`, nullable FK -> `members.id` `ON DELETE SET NULL`)
- `created_at`, `updated_at`

### `bonus_grants`
- `id` (`CHAR(36)` PK)
- `bonus_id` (`CHAR(36)`, FK -> `bonuses.id` `ON DELETE CASCADE`)
- `recipient_type` (`ENUM('member', 'department')`)
- `legacy_id` keeps numeric log IDs for the compatibility API.
- `points` (`BIGINT` signed, NOT NULL) — snapshotted at grant time (positive = bonus, negative = deduction/penalty). Editing a template `actions` row later never rewrites past bonus grants.
- `source_action_id` (`INT UNSIGNED`, nullable FK -> `actions.id` `ON DELETE SET NULL`) — origin metadata only (usage counts / UI display; never joined to calculate points).
- `label_en`, `label_ar` (`VARCHAR(150)`, nullable)

### `bonus_grant_recipients`
- `id` (`CHAR(36)` PK)
- `bonus_grant_id` (`CHAR(36)`, FK -> `bonus_grants.id` `ON DELETE CASCADE`)
- `member_id` (`INT UNSIGNED`, nullable FK -> `members.id` `ON DELETE RESTRICT`)
- `department_id` (`INT UNSIGNED`, nullable FK -> `departments.id` `ON DELETE CASCADE`)
- `given_at` (`DATETIME` NOT NULL) — preserved for staying recipients on edit (drives weekly push summaries without shifting old points into the current week).
- Constraints:
  - `CHECK ((member_id IS NOT NULL) <> (department_id IS NOT NULL))` (XOR)
  - Separate unique indexes `uq_bonus_grant_recipients_grant_member (bonus_grant_id, member_id)` and `uq_bonus_grant_recipients_grant_department (bonus_grant_id, department_id)` (required because MySQL treats `NULL != NULL` in a single composite unique index).

### Updates to existing tables
- `events.location_type` shrunk from `('online', 'on-site', 'none', 'hidden')` to `('online', 'on-site')`; `open_events` view rebuilt.
- `pipeline_penalties.applied_bonus_grant_id` (`CHAR(36)`, FK -> `bonus_grants.id` `ON DELETE SET NULL`) replaces `applied_log_id`.
- `push_notifications.bonus_id` (`CHAR(36)`, FK -> `bonuses.id` `ON DELETE CASCADE`) added so pushes for migrated fake events survive fake-event deletion and new bonus pushes carry a real FK.

---

## Points & Leaderboard Queries (`app/DB/points.py`)

- **Totals** (`get_members_points_semester`, `get_departments_points_semester`): computed with two independent CTEs (`event_pts` and `bonus_pts`) left-joined onto `members`/`departments` to avoid Cartesian row multiplication. Event-attached bonuses follow the event's current semester and count when it is published. Public and hidden standalone bonuses both count, except when their original points-only event was unpublished.
- **History** (`GET /points/members/{id}`, `GET /points/departments/{id}`):
  - Returns a clean chronological `history: list[PointsHistoryItem_model]` discriminated by `kind: "event" | "bonus"`, with `event_id` (clickable when set), `bonus_id`, `label_en`, `label_ar`, `points`, `occurred_at`, `end_datetime`, `location_type`.
  - Temporarily also returns deprecated `events: list[Event_model]` mapped from the same rows so already-shipped TestFlight iOS builds keep working without crashes or broken breakdown bars.

---

## API Endpoints

### New `/bonuses` router (`Require(Perm.POINTS_CUSTOM)`)
- `GET /bonuses` — list bonuses (`?semester`, `?standalone_only`, `?event_id`)
- `POST /bonuses` — atomically create a bonus + all member/department grants
- `GET /bonuses/{bonus_id}` — full detail with grants & recipients
- `PUT /bonuses/{bonus_id}` — atomically update metadata and sync grants (preserving recipient `given_at` and push dedupe keys)
- `DELETE /bonuses/{bonus_id}` — delete bonus and cascade grants/recipients
- `GET /bonuses/by-event/{event_id}` — read event-attached bonus grants (`/points/full`)
- `PUT /bonuses/by-event/{event_id}` — atomically upsert/clear event-attached bonus grants

### Compatibility shims (Phase 1 only)
- `app/routers/custom.py` (`/custom/*`) stays mounted as a thin shim backed by `bonuses` (never creates fake events or logs) so shipped TestFlight iOS admin builds calling `POST /custom/members` and `GET /custom/members/{event_id}` continue working.
- `GET /events/me` returns `bonuses: list[MemberBonus_model]` and also projects those awards into the legacy `participated` list. The numeric IDs match the ones in `/points/*` history.

---

## Migration rollback

The upgrade checks for event workflow records attached to points-only events before deleting them. If it finds a dependent record such as a form, it stops before changing the schema. Both directions remap penalty references, push causes, and push dedupe keys.

Downgrade reconstructs the legacy ledger from the current bonuses, including awards written after upgrade. It preserves semester totals, old event and log IDs where a row maps back directly, and member award timestamps. Split recipient lists become separate logs and custom labels fit the old action field lengths, so the reconstructed ledger can have a different row layout.

## Release Rollout Plan

1. **Phase 1 (this branch — `t3/separate-bonuses-points-model`):**
   - Merge **Backend** first into `dev` (runs `alembic upgrade head` on staging; verifies migration on a staging copy of prod).
   - Merge **Admin** and **Website** into `dev` (Admin uses `/bonuses`, Website uses `history` on `/points/*` and `bonuses` on `/events/me`).
   - Ship the `dev` -> `main` release together when ready. Existing TestFlight iOS builds keep working seamlessly via the `/custom/*` shim and `events` compat field on `/points/*`.
2. **Phase 2 (after `gdg-app` TestFlight update ships):**
   - Update `gdg-app` to read `history` on `/points/{members,departments}/{id}`, `bonuses` on `/events/me`, and call `/bonuses` in its admin portal.
   - Once old TestFlight builds expire, delete `app/routers/custom.py`, the deprecated `events` compat list on `/points/*`, and the legacy `participated` field on `/events/me`.
   - The event lifecycle shims for the same old builds ([EVENT_LIFECYCLE_COMPAT.md](EVENT_LIFECYCLE_COMPAT.md)) go at the same time.
3. **Next refactor (Problem 2 — Points Versioning / `event_actions`):**
   - Separate PR introducing `event_actions` + `event_action_recipients` with inlined `action_points`, resolving `HARDCODED_ACTION_IDS.md` and GDG-53 delta semantics cleanly on top of this separated bonus model.
