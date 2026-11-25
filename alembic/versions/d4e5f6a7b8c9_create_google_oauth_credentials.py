"""Create google_oauth_credentials table for encrypted DB-backed OAuth tokens.

Revision ID: d4e5f6a7b8c9
Revises: 91bbc2a2a139
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision = "d4e5f6a7b8c9"
down_revision = "91bbc2a2a139"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "google_oauth_credentials",
        sa.Column("id", mysql.CHAR(36, charset="ascii", collation="ascii_bin"), primary_key=True),
        sa.Column("service", mysql.VARCHAR(32, charset="ascii", collation="ascii_bin"), nullable=False),
        sa.Column("encrypted_refresh_token", mysql.TEXT(), nullable=False),
        sa.Column("account_email", sa.String(length=255), nullable=False),
        sa.Column("connected_by", mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column("connected_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["connected_by"],
            ["members.id"],
            ondelete="SET NULL",
            onupdate="CASCADE",
            name="fk_google_oauth_credentials_connected_by",
        ),
    )
    op.create_index("uq_google_oauth_credentials_service", "google_oauth_credentials", ["service"], unique=True)
    op.create_index("fk_google_oauth_credentials_connected_by", "google_oauth_credentials", ["connected_by"])


def downgrade() -> None:
    op.drop_table("google_oauth_credentials")
