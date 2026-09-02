"""add meeting_url to events

Revision ID: d9e0f1a2b3c4
Revises: 9895c421c10b
Create Date: 2026-09-02 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql


revision: str = "d9e0f1a2b3c4"
down_revision: Union[str, Sequence[str], None] = "9895c421c10b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column(
            "meeting_url",
            mysql.VARCHAR(500, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("events", "meeting_url")
