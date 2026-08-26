"""create email_jobs table

Background email sends run after the response is sent, so a failure has nowhere
to surface - the caller already has its 200. This table records each send's
outcome so it can be shown rather than silently lost.

Revision ID: e7f8a9b0c1d2
Revises: c6d7e8f9a0b1
Create Date: 2026-08-26 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import INTEGER


revision: str = "e7f8a9b0c1d2"
down_revision: Union[str, Sequence[str], None] = "c6d7e8f9a0b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


JOB_TYPES = ("event-certificate", "manual-certificate", "custom-email", "direct-email", "blast")
STATUSES = ("queued", "running", "succeeded", "partial", "failed")


def upgrade() -> None:
    op.create_table(
        "email_jobs",
        sa.Column("id", INTEGER(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("job_type", sa.Enum(*JOB_TYPES), nullable=False),
        sa.Column("status", sa.Enum(*STATUSES), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("created_by", INTEGER(unsigned=True), nullable=False),
        sa.Column("event_id", INTEGER(unsigned=True), nullable=True),
        sa.Column("total", INTEGER(unsigned=True), server_default=sa.text("'0'"), nullable=False),
        sa.Column("succeeded", INTEGER(unsigned=True), server_default=sa.text("'0'"), nullable=False),
        sa.Column("failed", INTEGER(unsigned=True), server_default=sa.text("'0'"), nullable=False),
        sa.Column("error", sa.TEXT(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by"], ["members.id"], name="fk_email_jobs_created_by", ondelete="CASCADE", onupdate="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["event_id"], ["events.id"], name="fk_email_jobs_event", ondelete="CASCADE", onupdate="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("fk_email_jobs_created_by", "email_jobs", ["created_by"])
    op.create_index("fk_email_jobs_event", "email_jobs", ["event_id"])
    op.create_index("ix_email_jobs_created_at", "email_jobs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_email_jobs_created_at", table_name="email_jobs")
    op.drop_index("fk_email_jobs_event", table_name="email_jobs")
    op.drop_index("fk_email_jobs_created_by", table_name="email_jobs")
    op.drop_table("email_jobs")
