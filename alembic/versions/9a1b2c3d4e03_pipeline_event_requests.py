"""events pipeline: event requests

Revision ID: 9a1b2c3d4e03
Revises: 9a1b2c3d4e02
Create Date: 2026-09-28 12:20:00.000000

``event_requests`` (+ ``event_request_partners``): a department's request to
hold an event. The stage enum covers the whole path now, so later steps do not
need an enum migration.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "9a1b2c3d4e03"
down_revision: Union[str, Sequence[str], None] = "9a1b2c3d4e02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ID = mysql.INTEGER(unsigned=True)


def _text(length: int | None = None):
    if length is None:
        return mysql.TEXT(charset="utf8mb4", collation="utf8mb4_0900_ai_ci")
    return mysql.VARCHAR(length, charset="utf8mb4", collation="utf8mb4_0900_ai_ci")


def upgrade() -> None:
    op.create_table(
        "event_requests",
        sa.Column("id", ID, primary_key=True, autoincrement=True),
        sa.Column("department_id", ID, nullable=False),
        sa.Column("created_by", ID, nullable=False),
        sa.Column(
            "stage",
            sa.Enum(
                "draft", "in_review", "returned", "media", "ready", "published", "cancelled", name="eventrequeststage"
            ),
            nullable=False,
            server_default=sa.text("'draft'"),
        ),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("hold_expires_at", sa.DateTime(), nullable=True),
        sa.Column(
            "undated_reason", sa.Enum("hold_expired", "day_banned", name="eventrequestundatedreason"), nullable=True
        ),
        sa.Column("title", _text(150), nullable=True),
        sa.Column("description", _text(), nullable=True),
        sa.Column(
            "event_type",
            sa.Enum("course", "bootcamp", "meetup", "workshop", "competition", name="eventrequesttype"),
            nullable=True,
        ),
        sa.Column("presenter_name", _text(100), nullable=True),
        sa.Column("presenter_email", sa.String(150), nullable=True),
        sa.Column("day_modes", sa.JSON(), nullable=True),
        sa.Column("daily_start_time", sa.Time(), nullable=True),
        sa.Column("daily_end_time", sa.Time(), nullable=True),
        sa.Column("is_official", mysql.TINYINT(1), nullable=True),
        sa.Column("location_scope", sa.Enum("inside", "outside", name="eventrequestlocationscope"), nullable=True),
        sa.Column("audience", sa.Enum("male", "female", "mixed", "none", name="eventrequestaudience"), nullable=True),
        sa.Column(
            "registration", sa.Enum("acceptance", "open", "none", name="eventrequestregistration"), nullable=True
        ),
        sa.Column("expected_accepted", ID, nullable=True),
        sa.Column("help_needed", _text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        ),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
        sa.Column("event_id", ID, nullable=True),
        sa.ForeignKeyConstraint(
            ["department_id"], ["departments.id"], name="fk_event_requests_department", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["members.id"], name="fk_event_requests_created_by", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], name="fk_event_requests_event", ondelete="SET NULL"),
        sa.CheckConstraint("end_date >= start_date", name="ck_event_requests_dates"),
        sa.Index("ix_event_requests_dates", "start_date", "end_date"),
        sa.Index("ix_event_requests_department_stage", "department_id", "stage"),
        sa.Index("ix_event_requests_stage", "stage"),
    )

    op.create_table(
        "event_request_partners",
        sa.Column("request_id", ID, primary_key=True),
        sa.Column("department_id", ID, primary_key=True),
        sa.ForeignKeyConstraint(
            ["request_id"], ["event_requests.id"], name="fk_event_request_partners_request", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["department_id"], ["departments.id"], name="fk_event_request_partners_department", ondelete="RESTRICT"
        ),
    )


def downgrade() -> None:
    op.drop_table("event_request_partners")
    op.drop_table("event_requests")
