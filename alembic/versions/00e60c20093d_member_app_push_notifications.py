"""member app push notifications

Four new tables for the GDG Qassim iPhone app's notifications (docs/PUSH.md):

- ``member_devices``: one row per APNs device token; a token belongs to one
  member at a time.
- ``member_notification_prefs``: the app's four switches. No row means the
  defaults (reminders, acceptance and points on; club news off).
- ``push_broadcasts``: announcements organizers sent to members' phones.
- ``push_notifications``: the outbox and the sent log. ``dedupe_key`` is
  unique so a trigger that runs twice adds nothing.

All four start empty; nothing existing changes.

Revision ID: 00e60c20093d
Revises: e6040566c37c
Create Date: 2026-10-20 02:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = "00e60c20093d"
down_revision: Union[str, Sequence[str], None] = "e6040566c37c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ID = mysql.INTEGER(unsigned=True)
UUID = mysql.CHAR(36, charset="ascii", collation="ascii_bin")
TEXT_60 = mysql.VARCHAR(60, charset="utf8mb4", collation="utf8mb4_0900_ai_ci")
TEXT_180 = mysql.VARCHAR(180, charset="utf8mb4", collation="utf8mb4_0900_ai_ci")
NOW = sa.text("CURRENT_TIMESTAMP")
KINDS = ("reminder", "accepted", "points", "new_event", "broadcast")
STATUSES = ("pending", "sent", "skipped", "failed")


def upgrade() -> None:
    op.create_table(
        "member_devices",
        sa.Column("id", UUID, nullable=False),
        sa.Column("member_id", ID, nullable=False),
        sa.Column("token", mysql.VARCHAR(200, charset="ascii", collation="ascii_bin"), nullable=False),
        sa.Column("platform", sa.Enum("ios"), nullable=False),
        sa.Column("environment", sa.Enum("production", "development"), nullable=False),
        sa.Column("app_version", sa.String(20), nullable=False),
        sa.Column("app_build", sa.String(20), nullable=False),
        sa.Column("locale", sa.Enum("ar", "en"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=NOW, nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"], name="fk_member_devices_member", ondelete="CASCADE"),
    )
    op.create_index("uq_member_devices_token", "member_devices", ["token"], unique=True)
    # Its leading member_id also backs the member FK.
    op.create_index("ix_member_devices_member", "member_devices", ["member_id", "last_seen_at"])

    op.create_table(
        "member_notification_prefs",
        sa.Column("id", UUID, nullable=False),
        sa.Column("member_id", ID, nullable=False),
        sa.Column("remind", mysql.TINYINT(1), server_default=sa.text("'1'"), nullable=False),
        sa.Column("accept", mysql.TINYINT(1), server_default=sa.text("'1'"), nullable=False),
        sa.Column("points", mysql.TINYINT(1), server_default=sa.text("'1'"), nullable=False),
        sa.Column("news", mysql.TINYINT(1), server_default=sa.text("'0'"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["member_id"], ["members.id"], name="fk_member_notification_prefs_member", ondelete="CASCADE"
        ),
    )
    op.create_index("uq_member_notification_prefs_member", "member_notification_prefs", ["member_id"], unique=True)

    op.create_table(
        "push_broadcasts",
        sa.Column("id", UUID, nullable=False),
        sa.Column("sent_by", ID, nullable=True),
        sa.Column("title_ar", TEXT_60, nullable=False),
        sa.Column("body_ar", TEXT_180, nullable=False),
        sa.Column("title_en", TEXT_60, nullable=True),
        sa.Column("body_en", TEXT_180, nullable=True),
        sa.Column("url", sa.String(200), nullable=True),
        sa.Column("audience", sa.JSON(), nullable=False),
        sa.Column("recipient_count", ID, nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["sent_by"], ["members.id"], name="fk_push_broadcasts_sent_by", ondelete="SET NULL"),
    )
    op.create_index("fk_push_broadcasts_sent_by", "push_broadcasts", ["sent_by"])
    op.create_index("ix_push_broadcasts_created_at", "push_broadcasts", ["created_at"])

    op.create_table(
        "push_notifications",
        sa.Column("id", UUID, nullable=False),
        sa.Column("member_id", ID, nullable=False),
        sa.Column("kind", sa.Enum(*KINDS), nullable=False),
        sa.Column("dedupe_key", mysql.VARCHAR(191, charset="ascii", collation="ascii_bin"), nullable=False),
        sa.Column("event_id", ID, nullable=True),
        sa.Column("broadcast_id", UUID, nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.Enum(*STATUSES), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("attempts", mysql.SMALLINT(unsigned=True), server_default=sa.text("'0'"), nullable=False),
        sa.Column("not_before", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=NOW, nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.String(100), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"], name="fk_push_notifications_member", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], name="fk_push_notifications_event", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["broadcast_id"], ["push_broadcasts.id"], name="fk_push_notifications_broadcast", ondelete="CASCADE"
        ),
    )
    op.create_index("uq_push_notifications_dedupe_key", "push_notifications", ["dedupe_key"], unique=True)
    # Its leading member_id also backs the member FK.
    op.create_index("ix_push_notifications_member", "push_notifications", ["member_id", "created_at"])
    op.create_index("ix_push_notifications_status", "push_notifications", ["status", "not_before"])
    op.create_index("fk_push_notifications_event", "push_notifications", ["event_id"])
    op.create_index("fk_push_notifications_broadcast", "push_notifications", ["broadcast_id"])


def downgrade() -> None:
    # Reverse FK order: push_notifications points at push_broadcasts. Each
    # table's indexes go with it: dropping one that backs a foreign key on
    # its own would be MySQL error 1553.
    op.drop_table("push_notifications")
    op.drop_table("push_broadcasts")
    op.drop_table("member_notification_prefs")
    op.drop_table("member_devices")
