"""create event_content_links table

Event recordings and slides had nowhere to live. Members upload them to
Google Drive / YouTube themselves; the admin pastes the URL here with a
display label, so the Drive filename behind the link is never shown to
members. The leaderboard app's event pages render the published rows.

drive_file_id is reserved for the Phase 2 drag-drop Drive upload and stays
NULL until then.

Revision ID: a5b6c7d8e9f0
Revises: 267cb3fe563f
Create Date: 2026-10-08 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import CHAR, INTEGER, TINYINT, VARCHAR

revision: str = "a5b6c7d8e9f0"
down_revision: Union[str, Sequence[str], None] = "267cb3fe563f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID_CHAR = CHAR(36, charset="ascii", collation="ascii_bin")
KINDS = ("slides", "recording", "other")


def upgrade() -> None:
    op.create_table(
        "event_content_links",
        sa.Column("id", UUID_CHAR, nullable=False),
        sa.Column("event_id", INTEGER(unsigned=True), nullable=False),
        sa.Column("label", VARCHAR(200, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"), nullable=False),
        sa.Column("url", VARCHAR(500, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"), nullable=False),
        sa.Column("kind", sa.Enum(*KINDS), nullable=False),
        sa.Column("drive_file_id", VARCHAR(255, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"), nullable=True),
        sa.Column("published", TINYINT(1), server_default=sa.text("'1'"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("created_by", INTEGER(unsigned=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], name="fk_event_content_links_event", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["created_by"], ["members.id"], name="fk_event_content_links_created_by", ondelete="SET NULL"
        ),
    )
    # Members pick links by their label, so an event never holds two rows with
    # the same name. Column collation utf8mb4_0900_ai_ci makes it case- and
    # accent-insensitive; the API maps a violation to a 409. Its leading
    # event_id also backs the event FK, so no separate single-column index.
    op.create_index("uq_event_content_links_event_label", "event_content_links", ["event_id", "label"], unique=True)


def downgrade() -> None:
    # The created_by index backs a foreign key, so the constraint goes first.
    # Dropping the FK constraints before any index is also what keeps 1553
    # away: after the constraints are gone no index has a backing duty.
    op.drop_constraint("fk_event_content_links_created_by", table_name="event_content_links", type_="foreignkey")
    op.drop_constraint("fk_event_content_links_event", table_name="event_content_links", type_="foreignkey")
    op.drop_index("uq_event_content_links_event_label", table_name="event_content_links")
    op.drop_index("fk_event_content_links_created_by", table_name="event_content_links")
    op.drop_table("event_content_links")
