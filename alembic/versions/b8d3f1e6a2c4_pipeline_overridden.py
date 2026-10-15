"""pipeline: a super admin books over a request

A super admin can book days another request holds. That request is bumped:
back to a draft with no dates (to reschedule), or cancelled. Each enum gains
``overridden``: ``event_requests.undated_reason`` (why a draft has no dates),
``pipeline_notifications.kind`` and ``pipeline_history.action``.

Revision ID: b8d3f1e6a2c4
Revises: a7c2e9d4b1f3
Create Date: 2026-10-15 09:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "b8d3f1e6a2c4"
down_revision: Union[str, Sequence[str], None] = "a7c2e9d4b1f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

REASONS = ("hold_expired", "day_banned")
KINDS = (
    "request_received",
    "dates_banned",
    "hold_expired",
    "returned",
    "task_done",
    "media_received",
    "ready_to_publish",
    "dates_changed",
)
ACTIONS = (
    "booked",
    "redated",
    "details_edited",
    "brief_edited",
    "submitted",
    "returned",
    "resubmitted",
    "cancelled",
    "confirmation_edited",
    "dates_moved",
    "poster_uploaded",
    "task_done",
    "published",
    "hold_expired",
    "dates_banned",
    "penalty_grown",
    "days_banned",
    "days_unbanned",
    "event_deleted",
)
# (table, column, enum name, values before, nullable)
COLUMNS = (
    ("event_requests", "undated_reason", "eventrequestundatedreason", REASONS, True),
    ("pipeline_notifications", "kind", "pipelinenotificationkind", KINDS, False),
    ("pipeline_history", "action", "pipelinehistoryaction", ACTIONS, False),
)


def upgrade() -> None:
    for table, column, name, values, nullable in COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=sa.Enum(*values, name=name),
            type_=sa.Enum(*values, "overridden", name=name),
            existing_nullable=nullable,
        )


def downgrade() -> None:
    op.execute("UPDATE event_requests SET undated_reason = 'hold_expired' WHERE undated_reason = 'overridden'")
    op.execute("DELETE FROM pipeline_notifications WHERE kind = 'overridden'")
    op.execute("DELETE FROM pipeline_history WHERE action = 'overridden'")
    for table, column, name, values, nullable in COLUMNS:
        op.alter_column(
            table,
            column,
            existing_type=sa.Enum(*values, "overridden", name=name),
            type_=sa.Enum(*values, name=name),
            existing_nullable=nullable,
        )
