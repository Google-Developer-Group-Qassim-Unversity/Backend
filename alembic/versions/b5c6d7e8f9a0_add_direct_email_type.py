"""add 'direct' to email_logs.email_type (single-recipient direct-send tab)

Revision ID: b5c6d7e8f9a0
Revises: a4b5c6d7e8f9
Create Date: 2026-08-24 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b5c6d7e8f9a0"
down_revision: Union[str, Sequence[str], None] = "a4b5c6d7e8f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OLD_VALUES = ("event-certificate", "manual-certificate", "event_announcement", "acceptance", "blast")
NEW_VALUES = OLD_VALUES + ("direct",)


def upgrade() -> None:
    op.alter_column(
        "email_logs",
        "email_type",
        existing_type=sa.Enum(*OLD_VALUES),
        type_=sa.Enum(*NEW_VALUES),
        nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "email_logs",
        "email_type",
        existing_type=sa.Enum(*NEW_VALUES),
        type_=sa.Enum(*OLD_VALUES),
        nullable=False,
    )
