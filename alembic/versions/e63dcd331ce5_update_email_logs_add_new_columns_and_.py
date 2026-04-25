"""update email_logs add new columns and enum values

Revision ID: e63dcd331ce5
Revises: 681decf3a6d7
Create Date: 2026-04-23 16:27:44.758123

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "e63dcd331ce5"
down_revision: Union[str, Sequence[str], None] = "681decf3a6d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "email_logs",
        "email_type",
        existing_type=sa.Enum("certificates", "blast", "acceptance"),
        type_=sa.Enum("event-certificate", "manual-certificate", "event_announcement", "acceptance"),
        nullable=False,
    )

    op.add_column("email_logs", sa.Column("sent_by", mysql.INTEGER(unsigned=True), nullable=False))
    op.create_foreign_key(
        "fk_email_logs_sent_by", "email_logs", "members", ["sent_by"], ["id"], ondelete="CASCADE", onupdate="CASCADE"
    )
    op.create_index("fk_email_logs_sent_by", "email_logs", ["sent_by"])

    op.add_column("email_logs", sa.Column("data", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("email_logs", "data")

    op.drop_index("fk_email_logs_sent_by", table_name="email_logs")
    op.drop_constraint("fk_email_logs_sent_by", "email_logs", type_="foreignkey")
    op.drop_column("email_logs", "sent_by")

    op.alter_column(
        "email_logs",
        "email_type",
        existing_type=sa.Enum("event-certificate", "manual-certificate", "event_announcement", "acceptance"),
        type_=sa.Enum("certificates", "blast", "acceptance"),
        nullable=False,
    )
