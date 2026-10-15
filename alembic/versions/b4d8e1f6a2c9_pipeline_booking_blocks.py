"""Booking blocks, and cancelling or deleting a request at any stage

- ``booking_blocks``: days Logistics closed together for one reason, and who
  closed them. Each ``booking_bans`` row now points at its block, which holds
  the reason and the member; the day rows lose their own ``reason`` and
  ``banned_by``. Existing closed days are grouped: a run of consecutive days
  with the same reason and the same member becomes one block. A day closed
  without a reason gets "No reason given".
- ``pipeline_notifications.kind`` gains ``cancelled``, for the teams a request
  reached before it was cancelled.
- ``pipeline_history.action`` gains ``request_deleted`` (a cancelled request
  deleted for good) and ``block_edited`` (a block's reason changed).

Revision ID: b4d8e1f6a2c9
Revises: b8d3f1e6a2c4
Create Date: 2026-10-15 10:00:00.000000

"""

import secrets
import uuid
from datetime import timedelta, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = "b4d8e1f6a2c9"
down_revision: Union[str, Sequence[str], None] = "b8d3f1e6a2c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ID = mysql.INTEGER(unsigned=True)
UUID = mysql.CHAR(36, charset="ascii", collation="ascii_bin")
REASON = mysql.VARCHAR(200, charset="utf8mb4", collation="utf8mb4_0900_ai_ci")
NO_REASON = "No reason given"

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
)
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
    "overridden",
)
NEW_ACTIONS = ("request_deleted", "block_edited")


def _uuid7(ms: int) -> str:
    """A time-ordered UUID (version 7), as app/DB/ids.py makes them, for the millisecond ``ms``."""
    rand = secrets.randbits(74)
    value = (ms << 80) | (0x7 << 76) | ((rand >> 62) << 64) | (0b10 << 62) | (rand & ((1 << 62) - 1))
    return str(uuid.UUID(int=value))


def upgrade() -> None:
    op.create_table(
        "booking_blocks",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("reason", REASON, nullable=False),
        sa.Column("blocked_by", ID, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(
            ["blocked_by"], ["members.id"], name="fk_booking_blocks_blocked_by", ondelete="RESTRICT"
        ),
    )
    op.add_column("booking_bans", sa.Column("block_id", UUID, nullable=True))

    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT date, reason, banned_by, created_at FROM booking_bans ORDER BY date")).all()
    block_id, last = None, None
    for day, reason, banned_by, created_at in rows:
        reason = (reason or "").strip() or NO_REASON
        if last is None or (last[0] + timedelta(days=1), last[1], last[2]) != (day, reason, banned_by):
            block_id = _uuid7(int(created_at.replace(tzinfo=timezone.utc).timestamp() * 1000))
            bind.execute(
                sa.text(
                    "INSERT INTO booking_blocks (id, reason, blocked_by, created_at) VALUES (:id, :reason, :by, :at)"
                ),
                {"id": block_id, "reason": reason, "by": banned_by, "at": created_at},
            )
        bind.execute(sa.text("UPDATE booking_bans SET block_id = :id WHERE date = :day"), {"id": block_id, "day": day})
        last = (day, reason, banned_by)

    op.alter_column("booking_bans", "block_id", existing_type=UUID, nullable=False)
    op.create_foreign_key(
        "fk_booking_bans_block", "booking_bans", "booking_blocks", ["block_id"], ["id"], ondelete="CASCADE"
    )
    op.drop_constraint("fk_booking_bans_banned_by", "booking_bans", type_="foreignkey")
    # MySQL keeps the index a foreign key created; drop it before the column.
    op.drop_index("fk_booking_bans_banned_by", table_name="booking_bans")
    op.drop_column("booking_bans", "banned_by")
    op.drop_column("booking_bans", "reason")

    op.alter_column(
        "pipeline_notifications",
        "kind",
        existing_type=sa.Enum(*KINDS, name="pipelinenotificationkind"),
        type_=sa.Enum(*KINDS, "cancelled", name="pipelinenotificationkind"),
        existing_nullable=False,
    )
    op.alter_column(
        "pipeline_history",
        "action",
        existing_type=sa.Enum(*ACTIONS, name="pipelinehistoryaction"),
        type_=sa.Enum(*ACTIONS, *NEW_ACTIONS, name="pipelinehistoryaction"),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.execute("DELETE FROM pipeline_history WHERE action IN ('request_deleted', 'block_edited')")
    op.alter_column(
        "pipeline_history",
        "action",
        existing_type=sa.Enum(*ACTIONS, *NEW_ACTIONS, name="pipelinehistoryaction"),
        type_=sa.Enum(*ACTIONS, name="pipelinehistoryaction"),
        existing_nullable=False,
    )
    op.execute("DELETE FROM pipeline_notifications WHERE kind = 'cancelled'")
    op.alter_column(
        "pipeline_notifications",
        "kind",
        existing_type=sa.Enum(*KINDS, "cancelled", name="pipelinenotificationkind"),
        type_=sa.Enum(*KINDS, name="pipelinenotificationkind"),
        existing_nullable=False,
    )

    op.add_column("booking_bans", sa.Column("reason", REASON, nullable=True))
    op.add_column("booking_bans", sa.Column("banned_by", ID, nullable=True))
    op.execute(
        "UPDATE booking_bans JOIN booking_blocks ON booking_blocks.id = booking_bans.block_id "
        "SET booking_bans.reason = booking_blocks.reason, booking_bans.banned_by = booking_blocks.blocked_by"
    )
    op.alter_column("booking_bans", "banned_by", existing_type=ID, nullable=False)
    op.create_foreign_key(
        "fk_booking_bans_banned_by", "booking_bans", "members", ["banned_by"], ["id"], ondelete="RESTRICT"
    )
    op.drop_constraint("fk_booking_bans_block", "booking_bans", type_="foreignkey")
    op.drop_index("fk_booking_bans_block", table_name="booking_bans")
    op.drop_column("booking_bans", "block_id")
    op.drop_table("booking_blocks")
