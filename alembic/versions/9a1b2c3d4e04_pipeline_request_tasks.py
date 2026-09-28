"""events pipeline: request tasks (briefs)

Revision ID: 9a1b2c3d4e04
Revises: 9a1b2c3d4e03
Create Date: 2026-09-28 12:30:00.000000

``event_request_tasks``: each team's part of a request - its brief and its progress.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "9a1b2c3d4e04"
down_revision: Union[str, Sequence[str], None] = "9a1b2c3d4e03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ID = mysql.INTEGER(unsigned=True)


def upgrade() -> None:
    op.create_table(
        "event_request_tasks",
        sa.Column("id", ID, primary_key=True, autoincrement=True),
        sa.Column("request_id", ID, nullable=False),
        sa.Column("team", sa.Enum("design", "logistics", "media", name="pipelineteam"), nullable=False),
        sa.Column(
            "status",
            sa.Enum("brief", "open", "returned", "done", name="eventrequesttaskstatus"),
            nullable=False,
            server_default=sa.text("'brief'"),
        ),
        sa.Column("brief", sa.JSON(), nullable=True),
        sa.Column("brief_version", mysql.SMALLINT(unsigned=True), nullable=True),
        sa.Column("opened_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("completed_by", ID, nullable=True),
        sa.ForeignKeyConstraint(
            ["request_id"], ["event_requests.id"], name="fk_event_request_tasks_request", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["completed_by"], ["members.id"], name="fk_event_request_tasks_completed_by", ondelete="RESTRICT"
        ),
        sa.Index("uq_event_request_tasks_team", "request_id", "team", unique=True),
        sa.Index("ix_event_request_tasks_team_status", "team", "status"),
    )


def downgrade() -> None:
    op.drop_table("event_request_tasks")
