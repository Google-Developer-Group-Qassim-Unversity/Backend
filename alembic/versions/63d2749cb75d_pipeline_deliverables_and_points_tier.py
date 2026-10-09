"""pipeline deliverables and points tier

- ``event_request_tasks.deliverable`` / ``deliverable_version``: what a team
  hands over - Logistics' confirmation, Design's poster.
- ``event_requests.department_action_id`` / ``member_action_id``: the points
  tier, picked by the requesting team instead of at publish.
- ``pipeline_notifications.kind`` gains ``dates_changed``, for when Logistics
  moves a request's dates.

Revision ID: 63d2749cb75d
Revises: 6a947b802753
Create Date: 2026-10-09 03:33:23.010791

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = "63d2749cb75d"
down_revision: Union[str, Sequence[str], None] = "6a947b802753"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ID = mysql.INTEGER(unsigned=True)
KINDS = (
    "request_received",
    "dates_banned",
    "hold_expired",
    "returned",
    "task_done",
    "media_received",
    "ready_to_publish",
)


def upgrade() -> None:
    op.add_column("event_request_tasks", sa.Column("deliverable", sa.JSON(), nullable=True))
    op.add_column("event_request_tasks", sa.Column("deliverable_version", mysql.SMALLINT(unsigned=True), nullable=True))

    op.add_column("event_requests", sa.Column("department_action_id", ID, nullable=True))
    op.add_column("event_requests", sa.Column("member_action_id", ID, nullable=True))
    op.create_foreign_key(
        "fk_event_requests_department_action",
        "event_requests",
        "actions",
        ["department_action_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_event_requests_member_action",
        "event_requests",
        "actions",
        ["member_action_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.alter_column(
        "pipeline_notifications",
        "kind",
        existing_type=sa.Enum(*KINDS, name="pipelinenotificationkind"),
        type_=sa.Enum(*KINDS, "dates_changed", name="pipelinenotificationkind"),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.execute("DELETE FROM pipeline_notifications WHERE kind = 'dates_changed'")
    op.alter_column(
        "pipeline_notifications",
        "kind",
        existing_type=sa.Enum(*KINDS, "dates_changed", name="pipelinenotificationkind"),
        type_=sa.Enum(*KINDS, name="pipelinenotificationkind"),
        existing_nullable=False,
    )

    op.drop_constraint("fk_event_requests_member_action", "event_requests", type_="foreignkey")
    op.drop_constraint("fk_event_requests_department_action", "event_requests", type_="foreignkey")
    # MySQL keeps the index a foreign key created; drop it before the column.
    op.drop_index("fk_event_requests_member_action", table_name="event_requests")
    op.drop_index("fk_event_requests_department_action", table_name="event_requests")
    op.drop_column("event_requests", "member_action_id")
    op.drop_column("event_requests", "department_action_id")

    op.drop_column("event_request_tasks", "deliverable_version")
    op.drop_column("event_request_tasks", "deliverable")
