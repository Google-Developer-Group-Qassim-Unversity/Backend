"""pipeline audit trail

- Clear names for who did what, each next to its time:
  ``event_requests.created_by/created_at`` become ``requested_by/requested_at``,
  ``event_request_tasks.completed_by/completed_at`` become ``done_by/done_at``.
- ``event_requests`` records who did each step next to when: ``submitted_by``,
  ``returned_by``, ``published_at``/``published_by``,
  ``cancelled_at``/``cancelled_by``. Published requests get theirs from the
  event they created.
- ``pipeline_history``: every step, edit and automatic change, with who did it.
  It starts empty; what happened before it is in the columns above.

Revision ID: f9cbc70ff702
Revises: 63d2749cb75d
Create Date: 2026-10-09 04:18:34.567164

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = "f9cbc70ff702"
down_revision: Union[str, Sequence[str], None] = "63d2749cb75d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ID = mysql.INTEGER(unsigned=True)
UUID = mysql.CHAR(36, charset="ascii", collation="ascii_bin")
ACTIONS = (
    "booked",
    "redated",
    "details_edited",
    "brief_edited",
    "submitted",
    "returned",
    "resubmitted",
    "cancelled",
    "confirmation_edited",
    "dates_moved",
    "poster_uploaded",
    "task_done",
    "published",
    "hold_expired",
    "dates_banned",
    "penalty_grown",
    "days_banned",
    "days_unbanned",
    "event_deleted",
)
# (column, foreign key) for each "who"; the "when" columns are added beside them.
WHO = (
    ("submitted_by", "fk_event_requests_submitted_by"),
    ("returned_by", "fk_event_requests_returned_by"),
    ("published_by", "fk_event_requests_published_by"),
    ("cancelled_by", "fk_event_requests_cancelled_by"),
)


# (table, old column, new column, old foreign key, new foreign key, nullable)
RENAMED = (
    (
        "event_requests",
        "created_by",
        "requested_by",
        "fk_event_requests_created_by",
        "fk_event_requests_requested_by",
        False,
    ),
    (
        "event_request_tasks",
        "completed_by",
        "done_by",
        "fk_event_request_tasks_completed_by",
        "fk_event_request_tasks_done_by",
        True,
    ),
)


def _rename_who(table: str, old: str, new: str, old_fk: str, new_fk: str, nullable: bool) -> None:
    op.drop_constraint(old_fk, table, type_="foreignkey")
    # The index MySQL made for the foreign key keeps its name; give it the new one.
    if old_fk in {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}:
        op.execute(f"ALTER TABLE {table} RENAME INDEX {old_fk} TO {new_fk}")
    op.alter_column(table, old, new_column_name=new, existing_type=ID, existing_nullable=nullable)
    op.create_foreign_key(new_fk, table, "members", [new], ["id"], ondelete="RESTRICT")


def upgrade() -> None:
    for renamed in RENAMED:
        _rename_who(*renamed)
    op.alter_column(
        "event_requests",
        "created_at",
        new_column_name="requested_at",
        existing_type=sa.DateTime(),
        existing_nullable=False,
        existing_server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    op.alter_column(
        "event_request_tasks",
        "completed_at",
        new_column_name="done_at",
        existing_type=sa.DateTime(),
        existing_nullable=True,
    )

    op.add_column("event_requests", sa.Column("submitted_by", ID, nullable=True))
    op.add_column("event_requests", sa.Column("returned_by", ID, nullable=True))
    op.add_column("event_requests", sa.Column("published_at", sa.DateTime(), nullable=True))
    op.add_column("event_requests", sa.Column("published_by", ID, nullable=True))
    op.add_column("event_requests", sa.Column("cancelled_at", sa.DateTime(), nullable=True))
    op.add_column("event_requests", sa.Column("cancelled_by", ID, nullable=True))
    for column, name in WHO:
        op.create_foreign_key(name, "event_requests", "members", [column], ["id"], ondelete="SET NULL")

    # The event a published request created records who created it and when.
    op.execute(
        "UPDATE event_requests r JOIN events e ON e.id = r.event_id "
        "SET r.published_by = e.created_by, r.published_at = e.created_at "
        "WHERE r.stage = 'published'"
    )

    op.create_table(
        "pipeline_history",
        sa.Column("id", UUID, primary_key=True),
        # No foreign key: deleting a published event deletes its request, and the history must outlive it.
        sa.Column("request_id", UUID, nullable=True),
        sa.Column("actor_id", ID, nullable=True),
        sa.Column("action", sa.Enum(*ACTIONS, name="pipelinehistoryaction"), nullable=False),
        sa.Column("at", sa.DateTime(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column(
            "request_title", mysql.VARCHAR(150, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"), nullable=True
        ),
        sa.Column("department_id", ID, nullable=True),
        sa.ForeignKeyConstraint(["actor_id"], ["members.id"], name="fk_pipeline_history_actor", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["department_id"], ["departments.id"], name="fk_pipeline_history_department", ondelete="SET NULL"
        ),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    op.create_index("ix_pipeline_history_request", "pipeline_history", ["request_id", "at"])
    op.create_index("ix_pipeline_history_actor", "pipeline_history", ["actor_id", "at"])
    op.create_index("fk_pipeline_history_department", "pipeline_history", ["department_id"])


def downgrade() -> None:
    op.drop_table("pipeline_history")
    for column, name in WHO:
        op.drop_constraint(name, "event_requests", type_="foreignkey")
        # MySQL keeps the index a foreign key created; drop it before the column.
        op.drop_index(name, table_name="event_requests")
    for column in ("cancelled_by", "cancelled_at", "published_by", "published_at", "returned_by", "submitted_by"):
        op.drop_column("event_requests", column)

    op.alter_column(
        "event_request_tasks",
        "done_at",
        new_column_name="completed_at",
        existing_type=sa.DateTime(),
        existing_nullable=True,
    )
    op.alter_column(
        "event_requests",
        "requested_at",
        new_column_name="created_at",
        existing_type=sa.DateTime(),
        existing_nullable=False,
        existing_server_default=sa.text("CURRENT_TIMESTAMP"),
    )
    for table, old, new, old_fk, new_fk, nullable in RENAMED:
        _rename_who(table, new, old, new_fk, old_fk, nullable)
