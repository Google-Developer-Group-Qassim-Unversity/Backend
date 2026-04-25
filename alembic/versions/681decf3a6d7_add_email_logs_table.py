"""add email_logs table

Revision ID: 681decf3a6d7
Revises: 4c1d47d1072c
Create Date: 2026-04-22 20:56:11.069700

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "681decf3a6d7"
down_revision: Union[str, Sequence[str], None] = "4c1d47d1072c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "email_logs",
        sa.Column("id", mysql.INTEGER(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("member_id", mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column("event_id", mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column("from_address", sa.Enum("info@kerneltics.com", "gdg.qu1@gmail.com"), nullable=False),
        sa.Column("sent_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("recipient_count", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("email_type", sa.Enum("certificates", "blast", "acceptance"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["member_id"], ["members.id"], ondelete="CASCADE", onupdate="CASCADE", name="fk_email_logs_member"
        ),
        sa.ForeignKeyConstraint(
            ["event_id"], ["events.id"], ondelete="CASCADE", onupdate="CASCADE", name="fk_email_logs_event"
        ),
    )

    op.create_index("fk_email_logs_member", "email_logs", ["member_id"])
    op.create_index("fk_email_logs_event", "email_logs", ["event_id"])


def downgrade() -> None:
    op.drop_index("fk_email_logs_event", table_name="email_logs")
    op.drop_index("fk_email_logs_member", table_name="email_logs")
    op.drop_table("email_logs")
