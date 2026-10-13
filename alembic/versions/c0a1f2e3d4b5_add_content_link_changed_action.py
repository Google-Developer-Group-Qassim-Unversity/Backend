"""add 'content_link_changed' to event_history.action

The content-link mutations (create, edit, delete on
/event/{id}/content-links) record an event_history row now, like every other
admin mutation on an event; the action column's enum needs the name to store
it. Appending at the end keeps every stored value readable - MySQL enums only
care about membership, and nothing reorders the existing names.

Revision ID: c0a1f2e3d4b5
Revises: a5b6c7d8e9f0
Create Date: 2026-10-12 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "c0a1f2e3d4b5"
down_revision: Union[str, Sequence[str], None] = "a5b6c7d8e9f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OLD_ACTIONS = (
    "created",
    "edited",
    "status_changed",
    "meeting_url_changed",
    "deleted",
    "attendance_marked",
    "attendance_scanned",
    "attendance_backfilled",
    "attendance_removed",
    "submissions_reviewed",
    "form_updated",
    "form_attached",
    "form_detached",
)
# Same order as app/DB/schema.py's EventHistoryAction: the new value sits
# where the model has it, so the schema comparison can never see a reorder.
NEW_ACTIONS = (
    "created",
    "edited",
    "status_changed",
    "meeting_url_changed",
    "content_link_changed",
    "deleted",
    "attendance_marked",
    "attendance_scanned",
    "attendance_backfilled",
    "attendance_removed",
    "submissions_reviewed",
    "form_updated",
    "form_attached",
    "form_detached",
)


def upgrade() -> None:
    op.alter_column(
        "event_history",
        "action",
        existing_type=sa.Enum(*OLD_ACTIONS, name="eventhistoryaction"),
        type_=sa.Enum(*NEW_ACTIONS, name="eventhistoryaction"),
        nullable=False,
    )


def downgrade() -> None:
    # Rows of the type being removed would otherwise become '' under MySQL's
    # non-strict enum coercion, which is worse than losing them.
    op.execute("DELETE FROM event_history WHERE action = 'content_link_changed'")
    op.alter_column(
        "event_history",
        "action",
        existing_type=sa.Enum(*NEW_ACTIONS, name="eventhistoryaction"),
        type_=sa.Enum(*OLD_ACTIONS, name="eventhistoryaction"),
        nullable=False,
    )
