"""events pipeline: department permissions and pipeline teams

Revision ID: 9a1b2c3d4e01
Revises: f3a4b5c6d7e8
Create Date: 2026-09-28 12:00:00.000000

- ``department_permissions``: members a leader or VP allowed to act for their
  department in the events pipeline. Leaders and VPs themselves need no row.
- ``pipeline_teams``: which department is Design, Logistics and Media.

The team map is seeded from the departments' exact Arabic names, once. A name
that does not match seeds nothing, and a super admin sets it from the app.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "9a1b2c3d4e01"
down_revision: Union[str, Sequence[str], None] = "f3a4b5c6d7e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ID = mysql.INTEGER(unsigned=True)

# The departments the plan names for each team (Notion: Events pipeline, PR 2).
SEED = {"design": "قسم التصميم", "logistics": "قسم ادارة البرامج واللوجستيات", "media": "قسم الاعلام والعلاقات العامة"}


def upgrade() -> None:
    op.create_table(
        "pipeline_teams",
        sa.Column("team", sa.Enum("design", "logistics", "media", name="pipelineteam"), primary_key=True),
        sa.Column("department_id", ID, nullable=False),
        sa.ForeignKeyConstraint(
            ["department_id"], ["departments.id"], name="fk_pipeline_teams_department", ondelete="RESTRICT"
        ),
        sa.Index("uq_pipeline_teams_department", "department_id", unique=True),
    )

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

    conn = op.get_bind()
    for team, ar_name in SEED.items():
        ids = conn.execute(sa.text("SELECT id FROM departments WHERE ar_name = :n"), {"n": ar_name}).scalars().all()
        if len(ids) == 1:
            conn.execute(
                sa.text("INSERT INTO pipeline_teams (team, department_id) VALUES (:t, :d)"), {"t": team, "d": ids[0]}
            )


def downgrade() -> None:
    op.drop_table("department_permissions")
    op.drop_table("pipeline_teams")
