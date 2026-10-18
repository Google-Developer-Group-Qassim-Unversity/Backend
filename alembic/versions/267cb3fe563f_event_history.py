"""event history

``event_history``: who did what to an event from /events - created, edited,
status and Meet link changes, deleted, attendance taken or removed,
registrations reviewed, forms changed. ``event_id`` has no foreign key so the
history outlives a deleted event.

Revision ID: 267cb3fe563f
Revises: f9cbc70ff702
Create Date: 2026-10-09 05:02:11.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = "267cb3fe563f"
down_revision: Union[str, Sequence[str], None] = "f9cbc70ff702"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ID = mysql.INTEGER(unsigned=True)
UUID = mysql.CHAR(36, charset="ascii", collation="ascii_bin")
ACTIONS = (
    "created",
    "edited",
    "status_changed",
    "meeting_url_changed",
    "deleted",
    "attendance_marked",
    "attendance_scanned",
    "attendance_backfilled",
    "attendance_removed",
    "submissions_reviewed",
    "form_updated",
    "form_attached",
    "form_detached",
)


def upgrade() -> None:
    op.create_table(
        "event_history",
        sa.Column("id", UUID, primary_key=True),
        # No foreign key: the history must outlive a deleted event.
        sa.Column("event_id", ID, nullable=False),
        sa.Column("actor_id", ID, nullable=True),
        sa.Column("action", sa.Enum(*ACTIONS, name="eventhistoryaction"), nullable=False),
        sa.Column("at", sa.DateTime(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column("event_name", mysql.VARCHAR(150, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"), nullable=True),
        sa.ForeignKeyConstraint(["actor_id"], ["members.id"], name="fk_event_history_actor", ondelete="SET NULL"),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index("ix_event_history_event", "event_history", ["event_id", "at"])
    op.create_index("ix_event_history_actor", "event_history", ["actor_id", "at"])


def downgrade() -> None:
    op.drop_table("event_history")
