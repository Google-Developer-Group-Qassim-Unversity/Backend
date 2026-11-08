"""push: rank notifications, and each member's standing on the semester's leaderboard

- ``push_notifications.kind`` gains three values (docs/PUSH.md):
  ``rank_top5`` (entered the top 5), ``rank_first`` (reached #1) and
  ``rank_left`` (left the top 5).
- ``member_rank_standings``: one row per member and semester, written the first
  time the member is in that semester's top 5. What the sweep last saw
  (``observed``, since when) and what the member's rank news is settled at
  (``told``); ``pushes`` keys the rank pushes' ``dedupe_key``. It starts empty:
  the sweep's first look at the current semester fills it without pushing.

The downgrade drops the table, deletes the rows of the new kinds (MySQL would
otherwise refuse, or blank, a value the narrower ENUM no longer has), then
narrows the column back.

Revision ID: 7b068beb7244
Revises: 80d73cc07c1a
Create Date: 2026-10-24 02:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = "7b068beb7244"
down_revision: Union[str, Sequence[str], None] = "80d73cc07c1a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ID = mysql.INTEGER(unsigned=True)
UUID = mysql.CHAR(36, charset="ascii", collation="ascii_bin")
OLD_KINDS = (
    "reminder",
    "accepted",
    "points",
    "new_event",
    "broadcast",
    "event_updated",
    "event_cancelled",
    "event_live",
    "registration_closing",
    "certificate",
    "event_content",
    "weekly_summary",
    "welcome",
)
ADDED_KINDS = ("rank_top5", "rank_first", "rank_left")
NEW_KINDS = OLD_KINDS + ADDED_KINDS
STANDINGS = ("out", "top5", "first")


def upgrade() -> None:
    op.alter_column(
        "push_notifications",
        "kind",
        existing_type=sa.Enum(*OLD_KINDS),
        type_=sa.Enum(*NEW_KINDS),
        existing_nullable=False,
    )
    op.create_table(
        "member_rank_standings",
        sa.Column("id", UUID, nullable=False),
        sa.Column("semester_id", UUID, nullable=False),
        sa.Column("member_id", ID, nullable=False),
        sa.Column("observed", sa.Enum(*STANDINGS), nullable=False),
        sa.Column("observed_since", sa.DateTime(), nullable=False),
        sa.Column("told", sa.Enum(*STANDINGS), nullable=False),
        sa.Column("told_at", sa.DateTime(), nullable=False),
        sa.Column("pushes", mysql.SMALLINT(unsigned=True), server_default=sa.text("'0'"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["semester_id"], ["semesters.id"], name="fk_member_rank_standings_semester", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["member_id"], ["members.id"], name="fk_member_rank_standings_member", ondelete="CASCADE"
        ),
    )
    # Its leading semester_id also backs the semester FK.
    op.create_index(
        "uq_member_rank_standings_semester_member", "member_rank_standings", ["semester_id", "member_id"], unique=True
    )
    op.create_index("fk_member_rank_standings_member", "member_rank_standings", ["member_id"])


def downgrade() -> None:
    # The table's indexes go with it: dropping one that backs a foreign key on
    # its own would be MySQL error 1553.
    op.drop_table("member_rank_standings")
    table = sa.table("push_notifications", sa.column("kind", sa.String))
    op.execute(table.delete().where(table.c.kind.in_(ADDED_KINDS)))
    op.alter_column(
        "push_notifications",
        "kind",
        existing_type=sa.Enum(*NEW_KINDS),
        type_=sa.Enum(*OLD_KINDS),
        existing_nullable=False,
    )
