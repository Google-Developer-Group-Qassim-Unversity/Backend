"""add email_templates table and blast email_type

Revision ID: c7d8e9f0a1b2
Revises: f1a2b3c4d5e6
Create Date: 2026-08-02 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "c7d8e9f0a1b2"
down_revision: Union[str, Sequence[str], None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "email_logs",
        "email_type",
        existing_type=sa.Enum("event-certificate", "manual-certificate", "event_announcement", "acceptance"),
        type_=sa.Enum("event-certificate", "manual-certificate", "event_announcement", "acceptance", "blast"),
        nullable=False,
    )

    op.create_table(
        "email_templates",
        sa.Column("id", mysql.INTEGER(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(150, collation="utf8mb4_0900_ai_ci"), nullable=False),
        sa.Column("subject", sa.String(255, collation="utf8mb4_0900_ai_ci"), nullable=False),
        sa.Column("html_content", mysql.LONGTEXT(charset="utf8mb4", collation="utf8mb4_0900_ai_ci"), nullable=False),
        sa.Column("preview_text", sa.String(255, collation="utf8mb4_0900_ai_ci"), nullable=True),
        sa.Column("created_by", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["created_by"], ["members.id"], ondelete="CASCADE", onupdate="CASCADE", name="fk_email_templates_created_by"
        ),
    )

    op.create_index("name", "email_templates", ["name"], unique=True)
    op.create_index("fk_email_templates_created_by", "email_templates", ["created_by"])


def downgrade() -> None:
    op.drop_index("fk_email_templates_created_by", table_name="email_templates")
    op.drop_index("name", table_name="email_templates")
    op.drop_table("email_templates")

    op.alter_column(
        "email_logs",
        "email_type",
        existing_type=sa.Enum("event-certificate", "manual-certificate", "event_announcement", "acceptance", "blast"),
        type_=sa.Enum("event-certificate", "manual-certificate", "event_announcement", "acceptance"),
        nullable=False,
    )
