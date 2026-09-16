"""add club assignments and department appearance

Revision ID: 8c9211f55b8b
Revises: c4d5e6f7a8b9
Create Date: 2026-09-13 17:48:37.478155

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = "8c9211f55b8b"
down_revision: Union[str, Sequence[str], None] = "c4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("departments", sa.Column("color", sa.String(7), nullable=False, server_default="#4285f4"))
    op.add_column("departments", sa.Column("icon", sa.String(32), nullable=False, server_default="users"))
    op.add_column("departments", sa.Column("leadership_enabled", mysql.TINYINT(1), nullable=False, server_default="1"))
    # Do not invent a creation date for existing departments. Future inserts get
    # a timestamp after the default is installed in a separate operation.
    op.add_column("departments", sa.Column("created_at", sa.DateTime(), nullable=True))
    op.alter_column(
        "departments", "created_at", existing_type=sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP")
    )
    op.add_column(
        "departments",
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        ),
    )

    # Preserve the existing department IDs, Arabic labels, points, and event
    # references. Runtime behavior will use the setting, never a name check.
    op.execute(
        sa.text("""
        UPDATE departments
        SET name = 'Board of Directors', leadership_enabled = 0
        WHERE TRIM(name) IN ('مجلس الإدارة', 'مجلس الادارة', 'Board of Directors')
           OR TRIM(ar_name) IN ('مجلس الإدارة', 'مجلس الادارة')
    """)
    )

    op.create_table(
        "club_assignments",
        sa.Column("id", mysql.INTEGER(unsigned=True), primary_key=True, autoincrement=True),
        sa.Column("member_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("department_id", mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column("role", sa.Enum("president", "leader", "deputy", "member"), nullable=False),
        sa.Column("president_slot", mysql.TINYINT(unsigned=True), nullable=True),
        sa.Column("starts_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
        sa.Column("ends_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("changed_by", sa.String(255), nullable=False),
        sa.Column("ended_by", sa.String(255), nullable=True),
        # MySQL's UNIQUE indexes allow multiple NULLs. Closed periods therefore
        # retain unlimited history while current rows must satisfy these keys.
        sa.Column(
            "current_scope_id",
            mysql.INTEGER(unsigned=True),
            sa.Computed("CASE WHEN ends_at IS NULL THEN COALESCE(department_id, 0) END"),
            nullable=True,
        ),
        sa.Column(
            "current_leadership_role",
            sa.String(6),
            sa.Computed("CASE WHEN ends_at IS NULL AND role IN ('leader', 'deputy') THEN role END"),
            nullable=True,
        ),
        sa.Column(
            "current_president_slot",
            mysql.TINYINT(unsigned=True),
            sa.Computed("CASE WHEN ends_at IS NULL THEN president_slot END"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"], name="fk_club_assignments_member", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["department_id"], ["departments.id"], name="fk_club_assignments_department", ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            "(role = 'president' AND department_id IS NULL AND president_slot IS NOT NULL "
            "AND president_slot IN (1, 2)) OR "
            "(role IN ('leader', 'deputy', 'member') AND department_id IS NOT NULL AND president_slot IS NULL)",
            name="ck_club_assignments_scope",
        ),
        sa.CheckConstraint("ends_at IS NULL OR ends_at >= starts_at", name="ck_club_assignments_period"),
        sa.Index("uq_club_assignments_current_member", "current_scope_id", "member_id", unique=True),
        sa.Index("uq_club_assignments_current_leader", "current_scope_id", "current_leadership_role", unique=True),
        sa.Index("uq_club_assignments_current_president", "current_president_slot", unique=True),
        sa.Index("ix_club_assignments_department_period", "department_id", "ends_at"),
        sa.Index("ix_club_assignments_member_period", "member_id", "starts_at"),
    )


def downgrade() -> None:
    # A schema downgrade intentionally removes assignment history. Keep the
    # Board's corrected name: its original English name cannot be reconstructed.
    op.drop_table("club_assignments")
    for column in ("updated_at", "created_at", "leadership_enabled", "icon", "color"):
        op.drop_column("departments", column)
