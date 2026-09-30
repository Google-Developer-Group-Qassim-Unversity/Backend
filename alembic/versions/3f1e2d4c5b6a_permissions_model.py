"""permissions: super admins, shared and department permissions, grants

Revision ID: 3f1e2d4c5b6a
Revises: 9a1b2c3d4e06
Create Date: 2026-10-01 12:00:00.000000

Access to the admin app moves off Clerk metadata and onto the club structure
(Notion: New permissions system, PR 2). Nothing reads these tables for access
yet except ``GET /access/me``.

- ``super_admins``: members who can do anything. Seeded empty; add the first
  one with ``scripts/add_super_admin.py``.
- ``shared_permissions``: what every leader and VP has.
- ``department_permissions``: what one department's leaders and VPs have.
- ``permission_grants``: what a leader or VP gave a member, for one semester.

The events pipeline's grants table was also called ``department_permissions``.
It is dropped first and its rows are discarded, not converted: the pipeline is
being rewritten onto these permissions. The downgrade recreates it empty.

Seeds: the shared permissions the catalogue suggests, and each pipeline team's
permissions for the department ``pipeline_teams`` maps to that team. A super
admin changes all of it later without a deploy.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "3f1e2d4c5b6a"
down_revision: Union[str, Sequence[str], None] = "9a1b2c3d4e06"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ID = mysql.INTEGER(unsigned=True)
UUID_CHAR = mysql.CHAR(36, charset="ascii", collation="ascii_bin")

SHARED = (
    "members.view",
    "events.create",
    "events.edit",
    "events.delete",
    "attendance.take",
    "attendance.backfill",
    "forms.manage",
    "submissions.review",
    "emails.event",
    "permissions.grant",
    "pipeline.request",
)

# pipeline_teams.team -> what that department's leaders and VPs get.
BY_TEAM = {
    "design": ("pipeline.design",),
    "logistics": ("pipeline.logistics", "pipeline.bans"),
    "media": ("pipeline.media", "emails.direct", "emails.blast", "emails.logs"),
}


def _added_at() -> sa.Column:
    return sa.Column("added_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP"))


def upgrade() -> None:
    op.drop_table("department_permissions")

    op.create_table(
        "super_admins",
        sa.Column("member_id", ID, primary_key=True, autoincrement=False),
        sa.Column("added_by", ID, nullable=True),
        _added_at(),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"], name="fk_super_admins_member", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["added_by"], ["members.id"], name="fk_super_admins_added_by", ondelete="SET NULL"),
    )

    op.create_table(
        "shared_permissions",
        sa.Column("permission", sa.String(64), primary_key=True),
        sa.Column("added_by", ID, nullable=True),
        _added_at(),
        sa.ForeignKeyConstraint(
            ["added_by"], ["members.id"], name="fk_shared_permissions_added_by", ondelete="SET NULL"
        ),
    )

    op.create_table(
        "department_permissions",
        sa.Column("department_id", ID, primary_key=True, autoincrement=False),
        sa.Column("permission", sa.String(64), primary_key=True),
        sa.Column("added_by", ID, nullable=True),
        _added_at(),
        sa.ForeignKeyConstraint(
            ["department_id"], ["departments.id"], name="fk_department_permissions_department", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["added_by"], ["members.id"], name="fk_department_permissions_added_by", ondelete="SET NULL"
        ),
    )

    op.create_table(
        "permission_grants",
        sa.Column("id", ID, primary_key=True, autoincrement=True),
        sa.Column("semester_id", UUID_CHAR, nullable=False),
        sa.Column("department_id", ID, nullable=False),
        sa.Column("member_id", ID, nullable=False),
        sa.Column("permission", sa.String(64), nullable=False),
        sa.Column("granted_by", ID, nullable=False),
        sa.Column("granted_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_by", ID, nullable=True),
        sa.Column(
            "active_key",
            mysql.TINYINT(unsigned=True),
            sa.Computed("CASE WHEN revoked_at IS NULL THEN 1 END", persisted=True),
        ),
        sa.ForeignKeyConstraint(
            ["semester_id"], ["semesters.id"], name="fk_permission_grants_semester", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["department_id"], ["departments.id"], name="fk_permission_grants_department", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"], name="fk_permission_grants_member", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["granted_by"], ["members.id"], name="fk_permission_grants_granted_by", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by"], ["members.id"], name="fk_permission_grants_revoked_by", ondelete="RESTRICT"
        ),
        sa.Index(
            "uq_permission_grants_active",
            "semester_id",
            "department_id",
            "member_id",
            "permission",
            "active_key",
            unique=True,
        ),
        sa.Index("ix_permission_grants_member", "member_id", "semester_id"),
        sa.Index("ix_permission_grants_department", "department_id"),
    )

    conn = op.get_bind()
    for permission in SHARED:
        conn.execute(sa.text("INSERT INTO shared_permissions (permission) VALUES (:p)"), {"p": permission})
    for team, department_id in conn.execute(sa.text("SELECT team, department_id FROM pipeline_teams")).all():
        for permission in BY_TEAM.get(team, ()):
            conn.execute(
                sa.text("INSERT INTO department_permissions (department_id, permission) VALUES (:d, :p)"),
                {"d": department_id, "p": permission},
            )


def downgrade() -> None:
    op.drop_table("permission_grants")
    op.drop_table("department_permissions")
    op.drop_table("shared_permissions")
    op.drop_table("super_admins")

    # The events pipeline's grants table, as 9a1b2c3d4e01 created it. Empty: its rows are gone.
    op.create_table(
        "department_permissions",
        sa.Column("id", ID, primary_key=True, autoincrement=True),
        sa.Column("member_id", ID, nullable=False),
        sa.Column("department_id", ID, nullable=False),
        sa.Column("granted_by", ID, nullable=False),
        sa.Column("granted_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_by", ID, nullable=True),
        sa.Column(
            "active_key",
            mysql.TINYINT(unsigned=True),
            sa.Computed("CASE WHEN revoked_at IS NULL THEN 1 END", persisted=True),
        ),
        sa.ForeignKeyConstraint(
            ["member_id"], ["members.id"], name="fk_department_permissions_member", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["department_id"], ["departments.id"], name="fk_department_permissions_department", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["granted_by"], ["members.id"], name="fk_department_permissions_granted_by", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["revoked_by"], ["members.id"], name="fk_department_permissions_revoked_by", ondelete="RESTRICT"
        ),
        sa.Index("uq_department_permissions_active", "member_id", "department_id", "active_key", unique=True),
        sa.Index("ix_department_permissions_department", "department_id"),
    )
