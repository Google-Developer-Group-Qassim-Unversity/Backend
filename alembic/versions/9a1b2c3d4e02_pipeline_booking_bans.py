"""events pipeline: booking bans

Revision ID: 9a1b2c3d4e02
Revises: 9a1b2c3d4e01
Create Date: 2026-09-28 12:10:00.000000

``booking_bans``: days Logistics closed to bookings, one row per day.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "9a1b2c3d4e02"
down_revision: Union[str, Sequence[str], None] = "9a1b2c3d4e01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "booking_bans",
        sa.Column("date", sa.Date(), primary_key=True),
        sa.Column("reason", mysql.VARCHAR(200, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"), nullable=True),
        sa.Column("banned_by", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["banned_by"], ["members.id"], name="fk_booking_bans_banned_by", ondelete="RESTRICT"),
    )


def downgrade() -> None:
    op.drop_table("booking_bans")
