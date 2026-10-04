"""deleting an event deletes the pipeline request that published it

Revision ID: 8e6f7a8b9c0d
Revises: 7d5e6f7a8b9c
Create Date: 2026-10-04 18:00:00.000000

``event_requests.event_id`` was ``ON DELETE SET NULL``: deleting a published
event left its request at ``published``, still holding its days. Now the request
goes with the event, and with it its tasks, partners, notifications and penalty
(all CASCADE from ``event_requests``). The event's points and the penalty's
discount already went with the event's logs. A day is taken only while a live
request covers it, so the days are free again.

Events deleted before this left orphans: requests still ``published`` with no
event (publish sets both together, so nothing else looks like that). They are
deleted here, the same as the cascade would have done.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "8e6f7a8b9c0d"
down_revision: Union[str, Sequence[str], None] = "7d5e6f7a8b9c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FK = "fk_event_requests_event"


def upgrade() -> None:
    op.execute("DELETE FROM event_requests WHERE stage = 'published' AND event_id IS NULL")
    op.drop_constraint(FK, "event_requests", type_="foreignkey")
    op.create_foreign_key(FK, "event_requests", "events", ["event_id"], ["id"], ondelete="CASCADE")


def downgrade() -> None:
    op.drop_constraint(FK, "event_requests", type_="foreignkey")
    op.create_foreign_key(FK, "event_requests", "events", ["event_id"], ["id"], ondelete="SET NULL")
