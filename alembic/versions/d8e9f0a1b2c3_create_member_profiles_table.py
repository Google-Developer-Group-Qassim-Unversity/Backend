"""create member_profiles table

Revision ID: d8e9f0a1b2c3
Revises: c7d8e9f0a1b2
Create Date: 2026-08-19 17:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "d8e9f0a1b2c3"
down_revision: Union[str, Sequence[str], None] = "c7d8e9f0a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "member_profiles",
        sa.Column("id", mysql.INTEGER(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("member_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("uuid", sa.String(length=64), nullable=False),
        sa.Column("theme_id", sa.String(length=50), server_default=sa.text("'gdg-blue'"), nullable=False),
        sa.Column(
            "name_language",
            sa.Enum("ar", "en", name="memberprofilesnamelanguage"),
            server_default=sa.text("'ar'"),
            nullable=False,
        ),
        sa.Column("bio", mysql.TEXT(charset="utf8mb4", collation="utf8mb4_0900_ai_ci"), nullable=True),
        sa.Column("social_links", sa.JSON(), nullable=True),
        sa.Column("visibility", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["member_id"],
            ["members.id"],
            ondelete="CASCADE",
            onupdate="CASCADE",
            name="fk_member_profiles_member",
        ),
        sa.UniqueConstraint("member_id", name="uq_member_profiles_member_id"),
        sa.UniqueConstraint("uuid", name="uq_member_profiles_uuid"),
    )

    op.create_index("idx_member_profiles_member_id", "member_profiles", ["member_id"], unique=True)
    op.create_index("idx_member_profiles_uuid", "member_profiles", ["uuid"], unique=True)


def downgrade() -> None:
    op.drop_table("member_profiles")
