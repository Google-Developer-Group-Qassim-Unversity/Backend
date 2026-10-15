"""pipeline calendar entries

``pipeline_calendar_entries``: one row per event request that has (or had) an
entry on the team's Google Calendar - whether a change still has to be pushed,
when it last was, and the last error if the push failed
(``app/services/pipeline_calendar.py``). ``request_id`` has no foreign key so
the row outlives a request deleted with its event, and the entry still comes
off the calendar.

Revision ID: b8d3f0e5c2a4
Revises: a7c2e9d4b1f3
Create Date: 2026-10-15 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = "b8d3f0e5c2a4"
down_revision: Union[str, Sequence[str], None] = "a7c2e9d4b1f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = mysql.CHAR(36, charset="ascii", collation="ascii_bin")


def upgrade() -> None:
    op.create_table(
        "pipeline_calendar_entries",
        sa.Column("id", UUID, primary_key=True),
        # No foreign key: the row must outlive a request deleted with its event.
        sa.Column("request_id", UUID, nullable=False),
        sa.Column("sync_pending", mysql.TINYINT(1), nullable=False, server_default=sa.text("'0'")),
        sa.Column("synced_at", sa.DateTime(), nullable=True),
        sa.Column("error", mysql.TEXT(charset="utf8mb4", collation="utf8mb4_0900_ai_ci"), nullable=True),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index("uq_pipeline_calendar_entries_request", "pipeline_calendar_entries", ["request_id"], unique=True)
    op.create_index("ix_pipeline_calendar_entries_pending", "pipeline_calendar_entries", ["sync_pending"])


def downgrade() -> None:
    op.drop_table("pipeline_calendar_entries")
