"""events pipeline: Design returns and late penalties

Revision ID: 9a1b2c3d4e06
Revises: 9a1b2c3d4e05
Create Date: 2026-09-28 12:50:00.000000

- ``event_requests``: the return (once, with notes and a 12-hour due time).
- ``pipeline_penalties``: points lost for fixing a returned request late,
  applied as a discount when the request is published.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "9a1b2c3d4e06"
down_revision: Union[str, Sequence[str], None] = "9a1b2c3d4e05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ID = mysql.INTEGER(unsigned=True)


def upgrade() -> None:
    op.add_column("event_requests", sa.Column("returned_at", sa.DateTime(), nullable=True))
    op.add_column(
        "event_requests",
        sa.Column("return_count", mysql.TINYINT(unsigned=True), nullable=False, server_default=sa.text("'0'")),
    )
    op.add_column(
        "event_requests",
        sa.Column("return_notes", mysql.TEXT(charset="utf8mb4", collation="utf8mb4_0900_ai_ci"), nullable=True),
    )
    op.add_column("event_requests", sa.Column("return_due_at", sa.DateTime(), nullable=True))

    op.create_table(
        "pipeline_penalties",
        sa.Column("id", ID, primary_key=True, autoincrement=True),
        sa.Column("request_id", ID, nullable=False),
        sa.Column("department_id", ID, nullable=False),
        sa.Column("late_days", ID, nullable=False),
        sa.Column("points", ID, nullable=False),
        sa.Column("reason", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        ),
        sa.Column("applied_log_id", ID, nullable=True),
        sa.ForeignKeyConstraint(
            ["request_id"], ["event_requests.id"], name="fk_pipeline_penalties_request", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["department_id"], ["departments.id"], name="fk_pipeline_penalties_department", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["applied_log_id"], ["logs.id"], name="fk_pipeline_penalties_log", ondelete="SET NULL"),
        sa.Index("uq_pipeline_penalties_request", "request_id", unique=True),
    )


def downgrade() -> None:
    op.drop_table("pipeline_penalties")
    op.drop_column("event_requests", "return_due_at")
    op.drop_column("event_requests", "return_notes")
    op.drop_column("event_requests", "return_count")
    op.drop_column("event_requests", "returned_at")
