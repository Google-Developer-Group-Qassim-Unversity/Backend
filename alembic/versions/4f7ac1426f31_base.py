"""base

Revision ID: 4f7ac1426f31
Revises:
Create Date: 2026-03-28 16:40:58.698615

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "4f7ac1426f31"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "actions",
        sa.Column("id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("action_name", mysql.VARCHAR(60, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"), nullable=False),
        sa.Column("points", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column(
            "action_type",
            mysql.ENUM("composite", "department", "member", "bonus", charset="utf8mb4", collation="utf8mb4_0900_ai_ci"),
            nullable=False,
        ),
        sa.Column(
            "ar_action_name", mysql.VARCHAR(100, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"), nullable=False
        ),
        sa.Column("order", sa.Integer, nullable=False, server_default=sa.text("'99'")),
        sa.Column("is_hidden", mysql.TINYINT(1), nullable=False, server_default=sa.text("'0'")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "departments",
        sa.Column("id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("type", mysql.ENUM("administrative", "practical"), nullable=False),
        sa.Column("ar_name", mysql.VARCHAR(100, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "events",
        sa.Column("id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("name", mysql.VARCHAR(150, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"), nullable=False),
        sa.Column("description", mysql.TEXT(charset="utf8mb4", collation="utf8mb4_0900_ai_ci"), nullable=True),
        sa.Column(
            "location_type",
            mysql.ENUM("online", "on-site", "none", "hidden", charset="utf8mb4", collation="utf8mb4_0900_ai_ci"),
            nullable=False,
        ),
        sa.Column("location", mysql.VARCHAR(100, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"), nullable=False),
        sa.Column("start_datetime", sa.DateTime, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("end_datetime", sa.DateTime, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column(
            "status",
            mysql.ENUM("draft", "open", "active", "closed", charset="utf8mb4", collation="utf8mb4_0900_ai_ci"),
            nullable=False,
        ),
        sa.Column("image_url", mysql.VARCHAR(100, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"), nullable=True),
        sa.Column("is_official", mysql.TINYINT(1), nullable=True, server_default=sa.text("'0'")),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("event_name", "events", ["name"])
    op.create_index("events_id_IDX", "events", ["id", "name"])

    op.create_table(
        "members",
        sa.Column("id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("name", sa.String(50), nullable=False),
        sa.Column("email", sa.String(100), nullable=True),
        sa.Column("phone_number", sa.String(20), nullable=True),
        sa.Column("uni_id", sa.String(50), nullable=False),
        sa.Column("gender", mysql.ENUM("Male", "Female"), nullable=False),
        sa.Column("uni_level", sa.Integer, nullable=False),
        sa.Column("uni_college", mysql.VARCHAR(100, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"), nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("is_authenticated", mysql.TINYINT(1), nullable=False, server_default=sa.text("'0'")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("uni_id", name="uni_id"),
    )

    op.create_table(
        "forms",
        sa.Column("id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("event_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column(
            "google_form_id", mysql.VARCHAR(100, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"), nullable=True
        ),
        sa.Column(
            "google_refresh_token", mysql.VARCHAR(500, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"), nullable=True
        ),
        sa.Column(
            "form_type",
            mysql.ENUM("none", "registration", "google", charset="utf8mb4", collation="utf8mb4_0900_ai_ci"),
            nullable=False,
        ),
        sa.Column("google_watch_id", sa.String(100), nullable=True),
        sa.Column(
            "google_responders_url",
            mysql.VARCHAR(150, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["event_id"], ["events.id"], name="forms_ibfk_1", ondelete="CASCADE", onupdate="CASCADE"
        ),
    )
    op.create_index("forms_unique_event_id", "forms", ["event_id"], unique=True)

    op.create_table(
        "logs",
        sa.Column("id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("action_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("event_id", mysql.INTEGER(unsigned=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["action_id"], ["actions.id"], name="logs_ibfk_1", ondelete="CASCADE", onupdate="CASCADE"
        ),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], name="fk_events", ondelete="CASCADE"),
    )
    op.create_index("action_id", "logs", ["action_id"])
    op.create_index("fk_events", "logs", ["event_id"])

    op.create_table(
        "role",
        sa.Column("id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("member_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column(
            "role",
            mysql.ENUM(
                "admin", "super_admin", "admin_points", "none", charset="utf8mb4", collation="utf8mb4_0900_ai_ci"
            ),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["member_id"], ["members.id"], name="fk_role_member", ondelete="CASCADE", onupdate="CASCADE"
        ),
    )
    op.create_index("fk_role_member", "role", ["member_id"])
    op.create_table(
        "departments_logs",
        sa.Column("id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("department_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("log_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["departments.id"],
            name="departments_logs_departments_FK",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["log_id"], ["logs.id"], name="departments_logs_logs_FK", ondelete="CASCADE", onupdate="CASCADE"
        ),
    )
    op.create_index("departments_logs_departments_FK", "departments_logs", ["department_id"])
    op.create_index("departments_logs_idx", "departments_logs", ["log_id", "department_id"])

    op.create_table(
        "members_logs",
        sa.Column("id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("member_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("log_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("date", sa.DateTime, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("member_id", "log_id", "date", name="unique_member_log_day"),
        sa.ForeignKeyConstraint(
            ["log_id"], ["logs.id"], name="members_logs_logs_FK", ondelete="CASCADE", onupdate="CASCADE"
        ),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"], name="fk_members_id"),
    )
    op.create_index("fk_members_id", "members_logs", ["member_id"])
    op.create_index("idx_members_logs_log_id", "members_logs", ["log_id"])

    op.create_table(
        "modifications",
        sa.Column("id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("log_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("type", mysql.ENUM("bonus", "discount"), nullable=False),
        sa.Column("value", mysql.INTEGER(unsigned=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["log_id"], ["logs.id"], name="modifications_ibfk_1", ondelete="CASCADE", onupdate="CASCADE"
        ),
    )
    op.create_index("log_id", "modifications", ["log_id"])

    op.create_table(
        "submissions",
        sa.Column("id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("form_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("member_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("is_accepted", mysql.TINYINT(1), nullable=False, server_default=sa.text("'0'")),
        sa.Column("google_submission_id", sa.String(100), nullable=True),
        sa.Column("google_submission_value", sa.JSON, nullable=True),
        sa.Column("submitted_at", sa.DateTime, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column(
            "submission_type",
            mysql.ENUM("none", "registration", "partial", "google", charset="utf8mb4", collation="utf8mb4_0900_ai_ci"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("member_id", "form_id", name="submissions_unique"),
        sa.ForeignKeyConstraint(
            ["form_id"], ["forms.id"], name="submissions_ibfk_1", ondelete="CASCADE", onupdate="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["member_id"], ["members.id"], name="submissions_ibfk_2", ondelete="CASCADE", onupdate="CASCADE"
        ),
    )
    op.create_index("from_id_member_id_idx", "submissions", ["form_id", "member_id"])


def downgrade() -> None:
    op.drop_index("from_id_member_id_idx", "submissions")
    op.drop_table("submissions")
    op.drop_index("log_id", "modifications")
    op.drop_table("modifications")
    op.drop_index("idx_members_logs_log_id", "members_logs")
    op.drop_index("fk_members_id", "members_logs")
    op.drop_table("members_logs")
    op.drop_index("departments_logs_idx", "departments_logs")
    op.drop_index("departments_logs_departments_FK", "departments_logs")
    op.drop_table("departments_logs")
    op.drop_index("fk_role_member", "role")
    op.drop_table("role")
    op.drop_index("fk_events", "logs")
    op.drop_index("action_id", "logs")
    op.drop_table("logs")
    op.drop_index("forms_unique_event_id", "forms")
    op.drop_table("forms")
    op.drop_table("members")
    op.drop_index("events_id_IDX", "events")
    op.drop_index("event_name", "events")
    op.drop_table("events")
    op.drop_table("departments")
    op.drop_table("actions")
