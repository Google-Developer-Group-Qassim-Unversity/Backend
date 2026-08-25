"""create semesters table

Revision ID: c6d7e8f9a0b1
Revises: b5c6d7e8f9a0
Create Date: 2026-08-25 00:00:00.000000

Moves the semester definitions out of app/config.py and into the database so
admins can add semesters and edit their date ranges without a redeploy. The
three semesters that were hardcoded in config are seeded here so behaviour is
unchanged right after the migration runs.
"""

from datetime import date
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "c6d7e8f9a0b1"
down_revision: Union[str, Sequence[str], None] = "b5c6d7e8f9a0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# (id, name, start_date, end_date, is_current) - previously config.SEMESTERS
SEEDED_SEMESTERS = [
    (475, "Summer 2026", "2026-06-28", "2026-08-20", 1),
    (472, "Spring 2026", "2026-01-18", "2026-05-31", 0),
    (471, "Fall 2025", "2025-08-24", "2026-01-17", 0),
]


def upgrade() -> None:
    semesters = op.create_table(
        "semesters",
        sa.Column("id", mysql.INTEGER(unsigned=True), primary_key=True, autoincrement=False),
        sa.Column("name", mysql.VARCHAR(100, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("is_current", mysql.TINYINT(1), nullable=False, server_default=sa.text("'0'")),
        sa.Column("is_public", mysql.TINYINT(1), nullable=False, server_default=sa.text("'1'")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        ),
    )

    op.bulk_insert(
        semesters,
        [
            {
                "id": semester_id,
                "name": name,
                "start_date": date.fromisoformat(start_date),
                "end_date": date.fromisoformat(end_date),
                "is_current": is_current,
                "is_public": 1,
            }
            for semester_id, name, start_date, end_date, is_current in SEEDED_SEMESTERS
        ],
    )


def downgrade() -> None:
    op.drop_table("semesters")
