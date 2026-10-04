"""record who is responsible for each event, and who created it

Revision ID: 8e6f7a8b9c0d
Revises: 7d5e6f7a8b9c
Create Date: 2026-10-04 18:00:00.000000

Two columns on events, both pointing at members.id:

    responsible_member_id   who answers for the event: the member who requested
                            it in the pipeline, or whoever created it directly
    created_by              who actually created the row: the POST /events/
                            caller, or whoever published the pipeline request

Events published from the pipeline are backfilled: responsible is the request's
creator. Who clicked publish was never stored, so created_by stays null for them,
as both columns do for every event created directly before this.

Both are ON DELETE SET NULL: deleting a member must not delete their events.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "8e6f7a8b9c0d"
down_revision: Union[str, Sequence[str], None] = "7d5e6f7a8b9c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("events", sa.Column("responsible_member_id", mysql.INTEGER(unsigned=True), nullable=True))
    op.add_column("events", sa.Column("created_by", mysql.INTEGER(unsigned=True), nullable=True))
    op.create_index("fk_events_responsible_member", "events", ["responsible_member_id"])
    op.create_index("fk_events_created_by", "events", ["created_by"])
    op.create_foreign_key(
        "fk_events_responsible_member", "events", "members", ["responsible_member_id"], ["id"], ondelete="SET NULL"
    )
    op.create_foreign_key("fk_events_created_by", "events", "members", ["created_by"], ["id"], ondelete="SET NULL")

    op.execute(
        """
        UPDATE events e
        JOIN event_requests r ON r.event_id = e.id
        SET e.responsible_member_id = r.created_by
        """
    )


def downgrade() -> None:
    op.drop_constraint("fk_events_created_by", "events", type_="foreignkey")
    op.drop_constraint("fk_events_responsible_member", "events", type_="foreignkey")
    op.drop_index("fk_events_created_by", table_name="events")
    op.drop_index("fk_events_responsible_member", table_name="events")
    op.drop_column("events", "created_by")
    op.drop_column("events", "responsible_member_id")
