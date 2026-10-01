"""events pipeline: find the teams by department name

Revision ID: 5b3c4d5e6f7a
Revises: 4a2b3c4d5e6f
Create Date: 2026-10-01 19:00:00.000000

Design, Logistics and Media are now the departments on the current semester's
roster whose names contain those words (app/DB/pipeline_teams.py), and their
leaders and VPs get the team's permissions from code (TEAM_PERMISSIONS). So:

- ``pipeline_teams``, the hand-set map, is dropped. Its rows are discarded.
- ``pipeline.teams``, the permission to set that map, is gone from the
  catalogue; any row holding it is deleted.

The downgrade recreates ``pipeline_teams`` empty, as 9a1b2c3d4e01 created it.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "5b3c4d5e6f7a"
down_revision: Union[str, Sequence[str], None] = "4a2b3c4d5e6f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("pipeline_teams")
    for table in ("shared_permissions", "department_permissions", "permission_grants"):
        op.execute(sa.text(f"DELETE FROM {table} WHERE permission = 'pipeline.teams'"))


def downgrade() -> None:
    op.create_table(
        "pipeline_teams",
        sa.Column("team", sa.Enum("design", "logistics", "media", name="pipelineteam"), primary_key=True),
        sa.Column("department_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["department_id"], ["departments.id"], name="fk_pipeline_teams_department", ondelete="RESTRICT"
        ),
        sa.Index("uq_pipeline_teams_department", "department_id", unique=True),
    )
