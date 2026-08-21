"""make uni_level and uni_college nullable

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-08-01 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import VARCHAR


revision: str = "f2a3b4c5d6e7"
down_revision: Union[str, Sequence[str], None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("members", "uni_level", existing_type=sa.Integer(), nullable=True)
    op.alter_column(
        "members",
        "uni_college",
        existing_type=VARCHAR(100, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "members",
        "uni_college",
        existing_type=VARCHAR(100, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"),
        nullable=False,
    )
    op.alter_column("members", "uni_level", existing_type=sa.Integer(), nullable=False)
