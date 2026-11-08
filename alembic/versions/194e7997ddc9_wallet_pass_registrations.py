"""wallet: Apple Wallet pass registrations and the welcome from the card

Apple's PassKit web service (docs/WALLET.md):

- ``wallet_pass_registrations``: a device holding a member's pass that asked
  to hear when it changes - Wallet's device library id, the pass's push
  token, the serial number, the member, and ``push_due_at`` /
  ``push_attempts`` while a pass update push is owed.
- ``member_profiles.pass_welcomed_at`` (NULL until one of the member's cards
  first registers: the welcome, once ever) and ``pass_updated_at`` (the pass's
  update tag), both naive UTC.

The table starts empty and both columns NULL: nothing existing changes, and
passes stay as they were until ``APPLE_PASS_WEB_SERVICE_URL`` is set.

Revision ID: 194e7997ddc9
Revises: 7b068beb7244
Create Date: 2026-10-24 02:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = "194e7997ddc9"
down_revision: Union[str, Sequence[str], None] = "7b068beb7244"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ID = mysql.INTEGER(unsigned=True)
UUID = mysql.CHAR(36, charset="ascii", collation="ascii_bin")
NOW = sa.text("CURRENT_TIMESTAMP")


def ascii_text(length: int) -> mysql.VARCHAR:
    return mysql.VARCHAR(length, charset="ascii", collation="ascii_bin")


def upgrade() -> None:
    op.create_table(
        "wallet_pass_registrations",
        sa.Column("id", UUID, nullable=False),
        sa.Column("member_id", ID, nullable=False),
        sa.Column("device_library_id", ascii_text(100), nullable=False),
        sa.Column("serial_number", ascii_text(40), nullable=False),
        sa.Column("push_token", ascii_text(200), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=NOW, nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=NOW, nullable=False),
        sa.Column("push_due_at", sa.DateTime(), nullable=True),
        sa.Column("push_attempts", mysql.SMALLINT(unsigned=True), server_default=sa.text("'0'"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["member_id"], ["members.id"], name="fk_wallet_pass_registrations_member", ondelete="CASCADE"
        ),
    )
    op.create_index(
        "uq_wallet_pass_registrations_device_serial",
        "wallet_pass_registrations",
        ["device_library_id", "serial_number"],
        unique=True,
    )
    # Its leading member_id also backs the member FK.
    op.create_index("ix_wallet_pass_registrations_member", "wallet_pass_registrations", ["member_id", "updated_at"])
    op.create_index("ix_wallet_pass_registrations_push_due", "wallet_pass_registrations", ["push_due_at"])

    op.add_column("member_profiles", sa.Column("pass_welcomed_at", sa.DateTime(), nullable=True))
    op.add_column("member_profiles", sa.Column("pass_updated_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("member_profiles", "pass_updated_at")
    op.drop_column("member_profiles", "pass_welcomed_at")
    op.drop_table("wallet_pass_registrations")
