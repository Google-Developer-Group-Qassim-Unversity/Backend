"""Separate bonuses from events into first-class bonuses tables.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-11-26 20:45:00.000000
"""

from collections import defaultdict
from datetime import datetime
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

from app.DB.ids import new_id

revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, Sequence[str], None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID_CHAR = mysql.CHAR(36, charset="ascii", collation="ascii_bin")
UTF8_150 = mysql.VARCHAR(150, charset="utf8mb4", collation="utf8mb4_0900_ai_ci")

LEGACY_ID_START = 2**32

OLD_LOCATION_TYPES = ("online", "on-site", "none", "hidden")
NEW_LOCATION_TYPES = ("online", "on-site")

# Production attendable action IDs (see docs/HARDCODED_ACTION_IDS.md).
# Action 108 ("مشاركة في بطولة") has action_type='bonus' in prod even though
# it is the attendance log for tournaments, so it must stay on events.
ATTENDABLE_ACTION_IDS = {76, 77, 78, 79, 87, 89, 108}
COMPOSITE_DEPT_ACTION_IDS = {51, 52, 53, 54, 86, 88, 90, 105}
COMPOSITE_MEMBER_ACTION_IDS = {76, 77, 78, 79, 87, 89, 91, 108}
GENERIC_BONUS_NAMES = {"bonus", "discount"}


def _create_open_events_view() -> None:
    op.execute("DROP VIEW IF EXISTS open_events")
    op.execute("""
        CREATE VIEW open_events AS
        SELECT
            e.id AS id,
            e.name AS name,
            e.description AS description,
            e.location_type AS location_type,
            e.location AS location,
            e.start_datetime AS start_datetime,
            e.end_datetime AS end_datetime,
            e.status AS status,
            e.level AS level,
            e.image_url AS image_url,
            e.meeting_url AS meeting_url,
            e.meeting_url_visibility AS meeting_url_visibility,
            e.is_official AS is_official,
            e.semester_id AS semester_id,
            e.created_at AS created_at,
            e.published_at AS published_at,
            e.cancelled_at AS cancelled_at,
            f.id AS form_id,
            f.form_type AS form_type,
            f.google_responders_url AS google_responders_url,
            f.registration_status AS registration_status
        FROM events e
        JOIN forms f ON f.event_id = e.id
        WHERE e.status = 'published'
    """)


def _label_pair(
    action_name: str | None, ar_action_name: str | None, action_id: int | None
) -> tuple[str | None, str | None, int | None]:
    """Return (label_en, label_ar, source_action_id) for a migrated log.

    Generic placeholder Bonus/Discount actions were only a workaround to hang
    modifications on; they carry no real reason or template identity.
    """
    if (action_name or "").strip().lower() in GENERIC_BONUS_NAMES:
        return None, None, None
    return action_name, ar_action_name, action_id


def _migrate_log_to_grants(
    bind: sa.Connection,
    bonus_id: str,
    log_row: dict,
    mod_delta: int,
    member_rows: list[tuple[int, datetime]],
    dept_rows: list[int],
) -> list[str]:
    """Create bonus_grant(s) and recipient rows for one legacy logs row.

    Returns the created grant id(s). Handles duplicate recipients gracefully by
    creating separate grants per occurrence count if a legacy log ever held
    repeated rows for the same recipient.
    """
    total_points = int(log_row["action_points"] or 0) + int(mod_delta or 0)
    label_en, label_ar, source_action_id = _label_pair(
        log_row["action_name"], log_row["ar_action_name"], log_row["action_id"]
    )
    created_at = log_row["event_created_at"] or log_row["event_start"]
    grant_ids: list[str] = []

    def grant_params(grant_id: str) -> dict:
        return {
            "id": grant_id,
            "bonus_id": bonus_id,
            "points": total_points,
            "source_action_id": source_action_id,
            "label_en": label_en,
            "label_ar": label_ar,
            "created_at": created_at,
            # Split grants get new compatibility IDs beyond the old INT range.
            "legacy_id": log_row["log_id"] if not grant_ids else None,
        }

    if member_rows:
        # Group by member_id preserving earliest/latest timestamps; if a member appeared
        # multiple times on the same custom log, split into separate grant rows so total
        # points and timestamps stay exact while respecting UNIQUE(bonus_grant_id, member_id).
        by_member: dict[int, list[datetime]] = defaultdict(list)
        for member_id, dt in member_rows:
            by_member[member_id].append(dt)
        max_rounds = max(len(dts) for dts in by_member.values())
        for round_idx in range(max_rounds):
            grant_id = new_id()
            bind.execute(
                sa.text(
                    "INSERT INTO bonus_grants (id, bonus_id, recipient_type, points, source_action_id, label_en, label_ar, created_at, legacy_id) "
                    "VALUES (:id, :bonus_id, 'member', :points, :source_action_id, :label_en, :label_ar, :created_at, :legacy_id)"
                ),
                grant_params(grant_id),
            )
            grant_ids.append(grant_id)
            for member_id, dts in by_member.items():
                if round_idx < len(dts):
                    bind.execute(
                        sa.text(
                            "INSERT INTO bonus_grant_recipients (id, bonus_grant_id, member_id, department_id, given_at) "
                            "VALUES (:id, :grant_id, :member_id, NULL, :given_at)"
                        ),
                        {"id": new_id(), "grant_id": grant_id, "member_id": member_id, "given_at": dts[round_idx]},
                    )

    if dept_rows:
        by_dept: dict[int, int] = defaultdict(int)
        for dept_id in dept_rows:
            by_dept[dept_id] += 1
        max_rounds = max(by_dept.values())
        for round_idx in range(max_rounds):
            grant_id = new_id()
            bind.execute(
                sa.text(
                    "INSERT INTO bonus_grants (id, bonus_id, recipient_type, points, source_action_id, label_en, label_ar, created_at, legacy_id) "
                    "VALUES (:id, :bonus_id, 'department', :points, :source_action_id, :label_en, :label_ar, :created_at, :legacy_id)"
                ),
                grant_params(grant_id),
            )
            grant_ids.append(grant_id)
            for dept_id, count in by_dept.items():
                if round_idx < count:
                    bind.execute(
                        sa.text(
                            "INSERT INTO bonus_grant_recipients (id, bonus_grant_id, member_id, department_id, given_at) "
                            "VALUES (:id, :grant_id, NULL, :department_id, :given_at)"
                        ),
                        {
                            "id": new_id(),
                            "grant_id": grant_id,
                            "department_id": dept_id,
                            "given_at": log_row["event_start"],
                        },
                    )

    return grant_ids


def _remap_log_references(bind: sa.Connection, log_id: int, bonus_id: str, grant_ids: list[str]) -> None:
    # Duplicate legacy recipients can span grants. Use the first matching grant
    # for each referenced recipient so the old push stays deduplicated.
    for grant_id in grant_ids:
        bind.execute(
            sa.text("""
            UPDATE pipeline_penalties p
            JOIN bonus_grant_recipients r ON r.bonus_grant_id = :grant_id
                AND r.department_id = p.department_id
            SET p.applied_bonus_grant_id = :grant_id
            WHERE p.applied_log_id = :log_id AND p.applied_bonus_grant_id IS NULL
        """),
            {"log_id": log_id, "grant_id": grant_id},
        )
        bind.execute(
            sa.text("""
            UPDATE push_notifications p
            JOIN bonus_grant_recipients r ON r.bonus_grant_id = :grant_id AND r.member_id = p.member_id
            SET p.bonus_id = :bonus_id,
                p.dedupe_key = CONCAT('points:bonus:', :grant_id, ':', p.member_id),
                p.payload = JSON_SET(p.payload, '$.cause',
                    JSON_OBJECT('type', 'bonus', 'bonus_id', :bonus_id, 'grant_id', :grant_id))
            WHERE p.kind = 'points'
                AND p.dedupe_key = CONCAT('points:custom:', :log_id, ':', p.member_id)
        """),
            {"log_id": log_id, "grant_id": grant_id, "bonus_id": bonus_id},
        )


def _migrate_existing_data() -> None:
    bind = op.get_bind()

    # Load all modification deltas keyed by log_id.
    mod_rows = (
        bind.execute(
            sa.text(
                "SELECT log_id, "
                "SUM(CASE WHEN type = 'discount' THEN -ABS(COALESCE(value, 0)) "
                "         WHEN type = 'bonus' THEN ABS(COALESCE(value, 0)) "
                "         ELSE COALESCE(value, 0) END) AS delta "
                "FROM modifications GROUP BY log_id"
            )
        )
        .mappings()
        .all()
    )
    mod_by_log = {int(r["log_id"]): int(r["delta"] or 0) for r in mod_rows}

    # Load all pipeline penalties that have an applied_log_id.
    penalty_rows = (
        bind.execute(sa.text("SELECT applied_log_id FROM pipeline_penalties WHERE applied_log_id IS NOT NULL"))
        .mappings()
        .all()
    )
    penalty_log_ids = {int(r["applied_log_id"]) for r in penalty_rows}

    # Pre-fetch member_logs and department_logs grouped by log_id.
    ml_rows = bind.execute(sa.text("SELECT log_id, member_id, date FROM members_logs ORDER BY id ASC")).all()
    members_by_log: dict[int, list[tuple[int, datetime]]] = defaultdict(list)
    for log_id, member_id, dt in ml_rows:
        members_by_log[int(log_id)].append((int(member_id), dt))

    dl_rows = bind.execute(sa.text("SELECT log_id, department_id FROM departments_logs ORDER BY id ASC")).all()
    depts_by_log: dict[int, list[int]] = defaultdict(list)
    for log_id, dept_id in dl_rows:
        depts_by_log[int(log_id)].append(int(dept_id))

    # 1. Migrate standalone fake events (location_type IN ('none', 'hidden')).
    fake_events = (
        bind.execute(
            sa.text(
                "SELECT id, name, location_type, start_datetime, end_datetime, status, semester_id, created_by, created_at "
                "FROM events WHERE location_type IN ('none', 'hidden') ORDER BY id ASC"
            )
        )
        .mappings()
        .all()
    )

    fake_event_ids: list[int] = []
    for ev in fake_events:
        ev_id = int(ev["id"])
        fake_event_ids.append(ev_id)
        bonus_id = new_id()
        is_public = 0 if ev["location_type"] == "hidden" else 1
        bind.execute(
            sa.text(
                "INSERT INTO bonuses (id, title, semester_id, given_at, is_public, created_by, created_at, updated_at, "
                "legacy_id, legacy_status, legacy_end_datetime) "
                "VALUES (:id, :title, :semester_id, :given_at, :is_public, :created_by, :created_at, :updated_at, "
                ":legacy_id, :legacy_status, :legacy_end_datetime)"
            ),
            {
                "id": bonus_id,
                "title": ev["name"],
                "legacy_id": ev_id,
                "legacy_status": ev["status"],
                "legacy_end_datetime": ev["end_datetime"],
                "semester_id": ev["semester_id"],
                "given_at": ev["start_datetime"],
                "is_public": is_public,
                "created_by": ev["created_by"],
                "created_at": ev["created_at"],
                "updated_at": ev["created_at"],
            },
        )

        # Preserve push notification history before deleting the fake event.
        bind.execute(
            sa.text("UPDATE push_notifications SET bonus_id = :bonus_id, event_id = NULL WHERE event_id = :event_id"),
            {"bonus_id": bonus_id, "event_id": ev_id},
        )

        fake_logs = (
            bind.execute(
                sa.text(
                    "SELECT l.id AS log_id, l.action_id, a.action_name, a.ar_action_name, a.points AS action_points, "
                    "       a.action_type, e.start_datetime AS event_start, e.created_at AS event_created_at "
                    "FROM logs l JOIN events e ON e.id = l.event_id LEFT JOIN actions a ON a.id = l.action_id "
                    "WHERE l.event_id = :event_id ORDER BY l.id ASC"
                ),
                {"event_id": ev_id},
            )
            .mappings()
            .all()
        )

        for fake_log in fake_logs:
            log_id = int(fake_log["log_id"])
            grant_ids = _migrate_log_to_grants(
                bind,
                bonus_id,
                dict(fake_log),
                mod_by_log.get(log_id, 0),
                members_by_log.get(log_id, []),
                depts_by_log.get(log_id, []),
            )
            _remap_log_references(bind, log_id, bonus_id, grant_ids)

    if fake_event_ids:
        bind.execute(
            sa.text("DELETE FROM events WHERE id IN :ids").bindparams(sa.bindparam("ids", expanding=True)),
            {"ids": fake_event_ids},
        )

    # 2. Migrate custom bonus/penalty logs attached to real events (online / on-site).
    real_logs = (
        bind.execute(
            sa.text(
                "SELECT l.id AS log_id, l.event_id, l.action_id, a.action_name, a.ar_action_name, "
                "       a.points AS action_points, a.action_type, "
                "       e.name AS event_name, e.start_datetime AS event_start, "
                "       e.semester_id, e.created_by, e.created_at AS event_created_at "
                "FROM logs l "
                "JOIN events e ON e.id = l.event_id "
                "LEFT JOIN actions a ON a.id = l.action_id "
                "WHERE e.location_type IN ('online', 'on-site') "
                "ORDER BY l.event_id ASC, l.id ASC"
            )
        )
        .mappings()
        .all()
    )

    logs_by_event: dict[int, list[dict]] = defaultdict(list)
    for r in real_logs:
        logs_by_event[int(r["event_id"])].append(dict(r))

    custom_log_ids_to_delete: list[int] = []
    for ev_id, ev_logs in logs_by_event.items():
        # Identify the event's primary organizing (department) log and attendance (member) log.
        primary_dept_log_id: int | None = None
        primary_member_log_id: int | None = None

        for log in ev_logs:
            lid = int(log["log_id"])
            aid = int(log["action_id"]) if log["action_id"] is not None else -1
            if lid in penalty_log_ids:
                continue
            if primary_dept_log_id is None and aid in COMPOSITE_DEPT_ACTION_IDS:
                primary_dept_log_id = lid
            if primary_member_log_id is None and aid in COMPOSITE_MEMBER_ACTION_IDS:
                primary_member_log_id = lid

        # Fallback for non-prod/seeded databases whose action IDs aren't the hardcoded prod IDs.
        for log in ev_logs:
            lid = int(log["log_id"])
            if lid in penalty_log_ids:
                continue
            atype = log["action_type"]
            if primary_dept_log_id is None and atype in ("composite", "department") and depts_by_log.get(lid):
                primary_dept_log_id = lid
            if primary_member_log_id is None and atype == "member":
                primary_member_log_id = lid

        custom_logs: list[dict] = []
        for log in ev_logs:
            lid = int(log["log_id"])
            aid = int(log["action_id"]) if log["action_id"] is not None else -1
            if lid == primary_dept_log_id or lid == primary_member_log_id:
                continue
            is_penalty = lid in penalty_log_ids
            is_bonus_action = log["action_type"] == "bonus" and aid not in ATTENDABLE_ACTION_IDS
            is_extra_with_recipients = bool(members_by_log.get(lid) or depts_by_log.get(lid))
            if is_penalty or is_bonus_action or is_extra_with_recipients:
                custom_logs.append(log)

        if not custom_logs:
            continue

        first = custom_logs[0]
        bonus_id = new_id()
        bind.execute(
            sa.text(
                "INSERT INTO bonuses (id, title, event_id, semester_id, given_at, is_public, created_by, created_at, updated_at) "
                "VALUES (:id, :title, :event_id, :semester_id, :given_at, 1, :created_by, :created_at, :updated_at)"
            ),
            {
                "id": bonus_id,
                "title": first["event_name"],
                "event_id": ev_id,
                "semester_id": first["semester_id"],
                "given_at": first["event_start"],
                "created_by": first["created_by"],
                "created_at": first["event_created_at"],
                "updated_at": first["event_created_at"],
            },
        )

        for log in custom_logs:
            log_id = int(log["log_id"])
            grant_ids = _migrate_log_to_grants(
                bind,
                bonus_id,
                log,
                mod_by_log.get(log_id, 0),
                members_by_log.get(log_id, []),
                depts_by_log.get(log_id, []),
            )
            _remap_log_references(bind, log_id, bonus_id, grant_ids)
            custom_log_ids_to_delete.append(log_id)

    if custom_log_ids_to_delete:
        bind.execute(
            sa.text("DELETE FROM logs WHERE id IN :ids").bindparams(sa.bindparam("ids", expanding=True)),
            {"ids": custom_log_ids_to_delete},
        )


def _add_legacy_sequence(table: str) -> None:
    # Only compatibility IDs use the sequence. UUIDs remain the primary keys.
    op.create_index(f"uq_{table}_legacy_id", table, ["legacy_id"], unique=True)
    op.execute(f"ALTER TABLE {table} MODIFY legacy_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT")
    op.execute(f"ALTER TABLE {table} AUTO_INCREMENT = {LEGACY_ID_START}")


def _check_fake_event_dependencies() -> None:
    # A points-only event has no event workflow. Fail before MySQL's implicit
    # DDL commit if deleting one would also delete an unrelated record.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    quote = bind.dialect.identifier_preparer.quote
    broken_penalty = bind.execute(
        sa.text("""
        SELECT p.id
        FROM pipeline_penalties p
        JOIN logs l ON l.id = p.applied_log_id
        WHERE l.event_id IS NULL OR NOT EXISTS (
            SELECT 1 FROM departments_logs dl WHERE dl.log_id = l.id AND dl.department_id = p.department_id
        )
        LIMIT 1
    """)
    ).scalar()
    if broken_penalty is not None:
        raise RuntimeError(f"Applied pipeline penalty {broken_penalty} has no matching event and department award")

    for table in inspector.get_table_names():
        if table in {"logs", "push_notifications"}:
            continue
        for fk in inspector.get_foreign_keys(table):
            if fk["referred_table"] != "events":
                continue
            column = quote(fk["constrained_columns"][0])
            event_id = bind.execute(
                sa.text(
                    f"SELECT e.id FROM {quote(table)} child JOIN events e ON child.{column} = e.id "
                    "WHERE e.location_type IN ('none', 'hidden') LIMIT 1"
                )
            ).scalar()
            if event_id is not None:
                raise RuntimeError(f"Legacy bonus event {event_id} has dependent rows in {table}")


def upgrade() -> None:
    _check_fake_event_dependencies()
    # 1. Create bonuses, bonus_grants, and bonus_grant_recipients tables.
    op.create_table(
        "bonuses",
        sa.Column("id", UUID_CHAR, primary_key=True),
        sa.Column("title", UTF8_150, nullable=False),
        sa.Column("legacy_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column(
            "legacy_status", sa.Enum("draft", "published", "cancelled"), nullable=False, server_default="published"
        ),
        sa.Column("legacy_end_datetime", sa.DateTime(), nullable=True),
        sa.Column("event_id", mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column("semester_id", UUID_CHAR, nullable=False),
        sa.Column("given_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("is_public", mysql.TINYINT(1), nullable=False, server_default=sa.text("'1'")),
        sa.Column("created_by", mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], name="fk_bonuses_event", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["semester_id"], ["semesters.id"], name="fk_bonuses_semester", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by"], ["members.id"], name="fk_bonuses_created_by", ondelete="SET NULL"),
    )
    _add_legacy_sequence("bonuses")
    op.create_index("ix_bonuses_semester_given", "bonuses", ["semester_id", "given_at"])
    op.create_index("fk_bonuses_event", "bonuses", ["event_id"])
    op.create_index("fk_bonuses_created_by", "bonuses", ["created_by"])

    op.create_table(
        "bonus_grants",
        sa.Column("id", UUID_CHAR, primary_key=True),
        sa.Column("bonus_id", UUID_CHAR, nullable=False),
        sa.Column("legacy_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("recipient_type", sa.Enum("member", "department"), nullable=False),
        sa.Column("points", sa.BigInteger(), nullable=False),
        sa.Column("source_action_id", mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column("label_en", UTF8_150, nullable=True),
        sa.Column("label_ar", UTF8_150, nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["bonus_id"], ["bonuses.id"], name="fk_bonus_grants_bonus", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_action_id"], ["actions.id"], name="fk_bonus_grants_source_action", ondelete="SET NULL"
        ),
    )
    _add_legacy_sequence("bonus_grants")
    op.create_index("fk_bonus_grants_bonus", "bonus_grants", ["bonus_id"])
    op.create_index("fk_bonus_grants_source_action", "bonus_grants", ["source_action_id"])

    op.create_table(
        "bonus_grant_recipients",
        sa.Column("id", UUID_CHAR, primary_key=True),
        sa.Column("bonus_grant_id", UUID_CHAR, nullable=False),
        sa.Column("member_id", mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column("department_id", mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column("given_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(
            ["bonus_grant_id"], ["bonus_grants.id"], name="fk_bonus_grant_recipients_grant", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["member_id"], ["members.id"], name="fk_bonus_grant_recipients_member", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["department_id"], ["departments.id"], name="fk_bonus_grant_recipients_department", ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "(member_id IS NOT NULL) <> (department_id IS NOT NULL)", name="ck_bonus_grant_recipients_xor"
        ),
    )
    op.create_index(
        "uq_bonus_grant_recipients_grant_member", "bonus_grant_recipients", ["bonus_grant_id", "member_id"], unique=True
    )
    op.create_index(
        "uq_bonus_grant_recipients_grant_department",
        "bonus_grant_recipients",
        ["bonus_grant_id", "department_id"],
        unique=True,
    )
    op.create_index("ix_bonus_grant_recipients_member", "bonus_grant_recipients", ["member_id", "bonus_grant_id"])
    op.create_index(
        "ix_bonus_grant_recipients_department", "bonus_grant_recipients", ["department_id", "bonus_grant_id"]
    )

    # 2. Add applied_bonus_grant_id to pipeline_penalties and bonus_id to push_notifications.
    op.add_column("pipeline_penalties", sa.Column("applied_bonus_grant_id", UUID_CHAR, nullable=True))
    op.create_index("fk_pipeline_penalties_bonus_grant", "pipeline_penalties", ["applied_bonus_grant_id"])
    op.create_foreign_key(
        "fk_pipeline_penalties_bonus_grant",
        "pipeline_penalties",
        "bonus_grants",
        ["applied_bonus_grant_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column("push_notifications", sa.Column("bonus_id", UUID_CHAR, nullable=True))
    op.create_index("fk_push_notifications_bonus", "push_notifications", ["bonus_id"])
    op.create_foreign_key(
        "fk_push_notifications_bonus", "push_notifications", "bonuses", ["bonus_id"], ["id"], ondelete="CASCADE"
    )

    # 3. Migrate existing fake events and event-attached custom logs into the new tables.
    _migrate_existing_data()

    # 4. Drop legacy pipeline_penalties.applied_log_id.
    op.drop_constraint("fk_pipeline_penalties_log", "pipeline_penalties", type_="foreignkey")
    op.drop_column("pipeline_penalties", "applied_log_id")

    # 5. Shrink events.location_type to ('online', 'on-site') and rebuild open_events view.
    op.execute("DROP VIEW IF EXISTS open_events")
    op.alter_column(
        "events",
        "location_type",
        existing_type=sa.Enum(*OLD_LOCATION_TYPES),
        type_=sa.Enum(*NEW_LOCATION_TYPES),
        nullable=False,
    )
    _create_open_events_view()


def _restore_legacy_points() -> None:
    """Translate the current ledger back to legacy events, logs, and modifications."""
    bind = op.get_bind()
    restored_events: dict[str, int] = {}
    for row in bind.execute(sa.text("SELECT * FROM bonuses ORDER BY legacy_id")).mappings().all():
        event_id = row["event_id"]
        if event_id is None:
            result = bind.execute(
                sa.text("""
                INSERT INTO events (id, name, location_type, location, start_datetime, end_datetime,
                                    status, semester_id, created_by, created_at)
                VALUES (:old_id, :title, :location_type, 'none', :given_at, :end_datetime,
                        :legacy_status, :semester_id, :created_by, :created_at)
            """),
                {
                    **row,
                    "old_id": row["legacy_id"] if row["legacy_id"] < LEGACY_ID_START else None,
                    "location_type": "none" if row["is_public"] else "hidden",
                    "end_datetime": row["legacy_end_datetime"] or row["given_at"],
                },
            )
            event_id = row["legacy_id"] if row["legacy_id"] < LEGACY_ID_START else result.lastrowid
        restored_events[row["id"]] = event_id
        bind.execute(
            sa.text("""
            UPDATE push_notifications SET event_id = :event_id WHERE bonus_id = :bonus_id
        """),
            {"event_id": event_id, "bonus_id": row["id"]},
        )

    actions: dict[tuple[str, str], int] = {}
    for grant in bind.execute(sa.text("SELECT * FROM bonus_grants ORDER BY legacy_id")).mappings().all():
        fallback = ("Discount", "خصم") if grant["points"] < 0 else ("Bonus", "مكافأة")
        labels = ((grant["label_en"] or fallback[0])[:60], (grant["label_ar"] or fallback[1])[:100])
        if labels not in actions:
            params = {"en": labels[0], "ar": labels[1]}
            action_id = bind.execute(
                sa.text("""
                SELECT id FROM actions WHERE action_name = :en AND ar_action_name = :ar
                    AND action_type = 'bonus' AND points = 0 ORDER BY id LIMIT 1
            """),
                params,
            ).scalar()
            if action_id is None:
                action_id = bind.execute(
                    sa.text("""
                    INSERT INTO actions (action_name, ar_action_name, action_type, points, is_hidden)
                    VALUES (:en, :ar, 'bonus', 0, 1)
                """),
                    params,
                ).lastrowid
            actions[labels] = action_id

        inserted = bind.execute(
            sa.text("""
            INSERT INTO logs (id, event_id, action_id) VALUES (:old_id, :event_id, :action_id)
        """),
            {
                "old_id": grant["legacy_id"] if grant["legacy_id"] < LEGACY_ID_START else None,
                "event_id": restored_events[grant["bonus_id"]],
                "action_id": actions[labels],
            },
        )
        log_id = grant["legacy_id"] if grant["legacy_id"] < LEGACY_ID_START else inserted.lastrowid

        remaining = abs(grant["points"])
        while remaining:
            chunk = min(remaining, LEGACY_ID_START - 1)
            bind.execute(
                sa.text("""
                INSERT INTO modifications (log_id, type, value) VALUES (:id, :type, :value)
            """),
                {"id": log_id, "type": "discount" if grant["points"] < 0 else "bonus", "value": chunk},
            )
            remaining -= chunk
        params = {"log_id": log_id, "grant_id": grant["id"]}
        bind.execute(
            sa.text("""
            INSERT INTO members_logs (log_id, member_id, date)
            SELECT :log_id, member_id, given_at FROM bonus_grant_recipients
            WHERE bonus_grant_id = :grant_id AND member_id IS NOT NULL
        """),
            params,
        )
        bind.execute(
            sa.text("""
            INSERT INTO departments_logs (log_id, department_id)
            SELECT :log_id, department_id FROM bonus_grant_recipients
            WHERE bonus_grant_id = :grant_id AND department_id IS NOT NULL
        """),
            params,
        )
        bind.execute(
            sa.text("""
            UPDATE pipeline_penalties SET applied_log_id = :log_id WHERE applied_bonus_grant_id = :grant_id
        """),
            params,
        )
        bind.execute(
            sa.text("""
            UPDATE push_notifications
            SET dedupe_key = CONCAT('points:custom:', :log_id, ':', member_id),
                payload = JSON_SET(payload, '$.cause', JSON_OBJECT('type', 'custom', 'log_id', :log_id))
            WHERE kind = 'points'
                AND dedupe_key = CONCAT('points:bonus:', :grant_id, ':', member_id)
        """),
            params,
        )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS open_events")
    op.alter_column(
        "events",
        "location_type",
        existing_type=sa.Enum(*NEW_LOCATION_TYPES),
        type_=sa.Enum(*OLD_LOCATION_TYPES),
        nullable=False,
    )
    _create_open_events_view()

    op.add_column("pipeline_penalties", sa.Column("applied_log_id", mysql.INTEGER(unsigned=True), nullable=True))
    op.create_foreign_key(
        "fk_pipeline_penalties_log", "pipeline_penalties", "logs", ["applied_log_id"], ["id"], ondelete="SET NULL"
    )

    _restore_legacy_points()

    op.drop_constraint("fk_push_notifications_bonus", "push_notifications", type_="foreignkey")
    op.drop_index("fk_push_notifications_bonus", table_name="push_notifications")
    op.drop_column("push_notifications", "bonus_id")

    op.drop_constraint("fk_pipeline_penalties_bonus_grant", "pipeline_penalties", type_="foreignkey")
    op.drop_index("fk_pipeline_penalties_bonus_grant", table_name="pipeline_penalties")
    op.drop_column("pipeline_penalties", "applied_bonus_grant_id")

    op.drop_table("bonus_grant_recipients")
    op.drop_table("bonus_grants")
    op.drop_table("bonuses")
