"""create form_sync_jobs table

The Google Forms webhook's background sync used to only surface a failure via
logger.exception - this table gives it the same status-a-caller-can-poll
treatment `email_jobs` has for email sends.

Revision ID: 9895c421c10b
Revises: e7f8a9b0c1d2
Create Date: 2026-08-28 18:02:45.073746

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql
from sqlalchemy.dialects.mysql import INTEGER


# revision identifiers, used by Alembic.
revision: str = "9895c421c10b"
down_revision: Union[str, Sequence[str], None] = "e7f8a9b0c1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


STATUSES = ("queued", "running", "succeeded", "partial", "failed")


def upgrade() -> None:
    op.create_table(
        "form_sync_jobs",
        sa.Column("id", INTEGER(unsigned=True), autoincrement=True, nullable=False),
        sa.Column(
            "google_form_id", mysql.VARCHAR(64, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"), nullable=False
        ),
        sa.Column("status", sa.Enum(*STATUSES), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("total", INTEGER(unsigned=True), server_default=sa.text("'0'"), nullable=False),
        sa.Column("succeeded", INTEGER(unsigned=True), server_default=sa.text("'0'"), nullable=False),
        sa.Column("failed", INTEGER(unsigned=True), server_default=sa.text("'0'"), nullable=False),
        sa.Column("error", sa.TEXT(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_form_sync_jobs_created_at", "form_sync_jobs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_form_sync_jobs_created_at", table_name="form_sync_jobs")
    op.drop_table("form_sync_jobs")
