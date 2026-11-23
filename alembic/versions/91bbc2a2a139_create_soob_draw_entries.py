"""Create SOOB prize-draw entries.

Revision ID: 91bbc2a2a139
Revises: c0d4c848382b
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "91bbc2a2a139"
down_revision = "c0d4c848382b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "soob_draw_entries",
        sa.Column("id", mysql.CHAR(36, charset="ascii", collation="ascii_bin"), primary_key=True),
        sa.Column("member_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("entered_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("member_id", name="uq_soob_draw_entries_member"),
    )


def downgrade() -> None:
    op.drop_table("soob_draw_entries")
