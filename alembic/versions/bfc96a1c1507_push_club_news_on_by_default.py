"""push: club news on by default

The member app's four notification switches (``member_notification_prefs``)
are now all on by default: ``news`` joins ``remind``, ``accept`` and
``points`` (docs/PUSH.md). The column's default becomes 1, and every row
saved so far gets ``news = 1``: a row cannot tell a deliberate "off" from the
old default, the app has been public for less than two days, and the club's
rule is "on until the member switches it off". ``updated_at`` stays as it
was: it means when the member last saved.

The downgrade only puts the default back to 0. The rows switched on stay on:
which of them were off before cannot be known.

Revision ID: bfc96a1c1507
Revises: 00e60c20093d
Create Date: 2026-10-23 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = "bfc96a1c1507"
down_revision: Union[str, Sequence[str], None] = "00e60c20093d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "member_notification_prefs",
        "news",
        existing_type=mysql.TINYINT(1),
        existing_nullable=False,
        server_default=sa.text("'1'"),
    )
    op.execute("UPDATE member_notification_prefs SET news = 1 WHERE news = 0")


def downgrade() -> None:
    op.alter_column(
        "member_notification_prefs",
        "news",
        existing_type=mysql.TINYINT(1),
        existing_nullable=False,
        server_default=sa.text("'0'"),
    )
