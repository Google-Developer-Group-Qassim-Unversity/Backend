"""add unique constraint on members email

Revision ID: e1f2a3b4c5d6
Revises: c7d8e9f0a1b2
Create Date: 2026-07-31 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, Sequence[str], None] = "c7d8e9f0a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NOTE: prod has duplicate emails as of this writing - the member-record
    # cleanup must land before this migration can actually run against prod.
    op.create_index("ix_members_email", "members", ["email"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_members_email", table_name="members")
