"""add active to departments

Revision ID: f1a2b3c4d5e6
Revises: e63dcd331ce5
Create Date: 2026-07-21 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "e63dcd331ce5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "departments", sa.Column("active", mysql.TINYINT(1), nullable=False, server_default=sa.text("'1'"))
    )


def downgrade() -> None:
    op.drop_column("departments", "active")
