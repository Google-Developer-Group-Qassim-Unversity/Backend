"""events pipeline: notifications

Revision ID: 9a1b2c3d4e05
Revises: 9a1b2c3d4e04
Create Date: 2026-09-28 12:40:00.000000

``pipeline_notifications`` (per department) and ``pipeline_notification_reads``
(each person marks their own read).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "9a1b2c3d4e05"
down_revision: Union[str, Sequence[str], None] = "9a1b2c3d4e04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ID = mysql.INTEGER(unsigned=True)
KINDS = (
    "request_received",
    "dates_banned",
    "hold_expired",
    "returned",
    "task_done",
    "media_received",
    "ready_to_publish",
)


def upgrade() -> None:
    op.create_table(
        "pipeline_notifications",
        sa.Column("id", ID, primary_key=True, autoincrement=True),
        sa.Column("department_id", ID, nullable=False),
        sa.Column("request_id", ID, nullable=False),
        sa.Column("kind", sa.Enum(*KINDS, name="pipelinenotificationkind"), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(
            ["department_id"], ["departments.id"], name="fk_pipeline_notifications_department", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["request_id"], ["event_requests.id"], name="fk_pipeline_notifications_request", ondelete="CASCADE"
        ),
        sa.Index("ix_pipeline_notifications_department", "department_id", "created_at"),
    )
    op.create_table(
        "pipeline_notification_reads",
        sa.Column("notification_id", ID, primary_key=True),
        sa.Column("member_id", ID, primary_key=True),
        sa.Column("read_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(
            ["notification_id"],
            ["pipeline_notifications.id"],
            name="fk_pipeline_notification_reads_notification",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["member_id"], ["members.id"], name="fk_pipeline_notification_reads_member", ondelete="CASCADE"
        ),
    )


def downgrade() -> None:
    op.drop_table("pipeline_notification_reads")
    op.drop_table("pipeline_notifications")
