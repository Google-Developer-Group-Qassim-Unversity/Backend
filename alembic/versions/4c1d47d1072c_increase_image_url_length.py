"""increase image_url length

Revision ID: 4c1d47d1072c
Revises: b2c3d4e5f6g7
Create Date: 2026-04-08 12:39:05.845734

"""

from typing import Sequence, Union

from alembic import op
from sqlalchemy.dialects import mysql


revision: str = "4c1d47d1072c"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6g7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "events",
        "image_url",
        existing_type=mysql.VARCHAR(100, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"),
        type_=mysql.VARCHAR(500, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "events",
        "image_url",
        existing_type=mysql.VARCHAR(500, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"),
        type_=mysql.VARCHAR(100, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"),
        existing_nullable=True,
    )
