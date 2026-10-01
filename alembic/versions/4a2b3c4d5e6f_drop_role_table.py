"""permissions: drop the old role table

Revision ID: 4a2b3c4d5e6f
Revises: 3f1e2d4c5b6a
Create Date: 2026-10-01 18:00:00.000000

``role`` held the old club-wide roles (admin, super_admin, admin_points, none),
mirrored from Clerk metadata by the /manage-admins page. Nothing reads it any
more: access comes from the roster, ``super_admins`` and the permission tables,
and the wallet's admin card follows the roster. Its rows are discarded. The
downgrade recreates it empty, as 4f7ac1426f31 created it.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "4a2b3c4d5e6f"
down_revision: Union[str, Sequence[str], None] = "3f1e2d4c5b6a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("role")


def downgrade() -> None:
    op.create_table(
        "role",
        sa.Column("id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("member_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column(
            "role",
            mysql.ENUM(
                "admin", "super_admin", "admin_points", "none", charset="utf8mb4", collation="utf8mb4_0900_ai_ci"
            ),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["member_id"], ["members.id"], name="fk_role_member", ondelete="CASCADE", onupdate="CASCADE"
        ),
    )
    op.create_index("fk_role_member", "role", ["member_id"])
