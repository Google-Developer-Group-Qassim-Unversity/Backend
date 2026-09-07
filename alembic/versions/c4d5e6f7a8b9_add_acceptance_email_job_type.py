"""add 'acceptance' to email_jobs.job_type

Acceptance blasts used to send inline: the admin's browser held the request
open for the whole Gmail send, which is ~0.2s per recipient, against a flat
60s gateway timeout. They run as a background job now, like every other blast,
and a job needs a type of its own to show up correctly in GET /emails/jobs.

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-09-06 18:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, Sequence[str], None] = "b3c4d5e6f7a8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OLD_JOB_TYPES = ("event-certificate", "manual-certificate", "custom-email", "direct-email", "blast")
NEW_JOB_TYPES = OLD_JOB_TYPES + ("acceptance",)


def upgrade() -> None:
    op.alter_column(
        "email_jobs", "job_type", existing_type=sa.Enum(*OLD_JOB_TYPES), type_=sa.Enum(*NEW_JOB_TYPES), nullable=False
    )


def downgrade() -> None:
    # Rows of the type being removed would otherwise become '' under MySQL's
    # non-strict enum coercion, which is worse than losing them.
    op.execute("DELETE FROM email_jobs WHERE job_type = 'acceptance'")
    op.alter_column(
        "email_jobs", "job_type", existing_type=sa.Enum(*NEW_JOB_TYPES), type_=sa.Enum(*OLD_JOB_TYPES), nullable=False
    )
