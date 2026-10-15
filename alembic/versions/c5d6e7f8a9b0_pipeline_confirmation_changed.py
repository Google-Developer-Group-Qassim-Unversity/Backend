"""pipeline: Logistics explains what it changed

Logistics confirms the event as it was actually booked. When the confirmed
time, place or details differ from what the department asked for, Logistics
gives a reason, which goes in the email to the requesting department. The
reason lives in the confirmation JSON; this only adds the notification kind
for a change that kept the dates: ``pipeline_notifications.kind`` gains
``confirmation_changed``.

Revision ID: c5d6e7f8a9b0
Revises: b8d3f0e5c2a4
Create Date: 2026-10-15 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "c5d6e7f8a9b0"
down_revision: Union[str, Sequence[str], None] = "b8d3f0e5c2a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

KINDS = (
    "request_received",
    "dates_banned",
    "hold_expired",
    "returned",
    "task_done",
    "media_received",
    "ready_to_publish",
    "dates_changed",
    "overridden",
    "cancelled",
)


def upgrade() -> None:
    op.alter_column(
        "pipeline_notifications",
        "kind",
        existing_type=sa.Enum(*KINDS, name="pipelinenotificationkind"),
        type_=sa.Enum(*KINDS, "confirmation_changed", name="pipelinenotificationkind"),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.execute("DELETE FROM pipeline_notifications WHERE kind = 'confirmation_changed'")
    op.alter_column(
        "pipeline_notifications",
        "kind",
        existing_type=sa.Enum(*KINDS, "confirmation_changed", name="pipelinenotificationkind"),
        type_=sa.Enum(*KINDS, name="pipelinenotificationkind"),
        existing_nullable=False,
    )
