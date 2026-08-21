"""make uni_id nullable, add clerk_user_id to members

Revision ID: f8771428c487
Revises: d8e9f0a1b2c3
Create Date: 2026-07-28 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f8771428c487"
down_revision: Union[str, Sequence[str], None] = "d8e9f0a1b2c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("members", sa.Column("clerk_user_id", sa.String(255), nullable=True))
    op.create_index("ix_members_clerk_user_id", "members", ["clerk_user_id"], unique=True)
    op.alter_column("members", "uni_id", existing_type=sa.String(50), nullable=True)


def downgrade() -> None:
    op.alter_column("members", "uni_id", existing_type=sa.String(50), nullable=False)
    op.drop_index("ix_members_clerk_user_id", table_name="members")
    op.drop_column("members", "clerk_user_id")
