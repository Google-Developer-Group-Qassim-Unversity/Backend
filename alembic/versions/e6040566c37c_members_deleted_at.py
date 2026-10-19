"""members: deleted_at, for accounts their owner deleted

A member who deletes their account (``DELETE /members/me``) keeps their row,
anonymized: their points and attendance stay in event counts, and the row is
what attendance, roster history, the events pipeline and email logs point at.
``deleted_at`` marks it, and the leaderboards, member histories, the admin
member lists and the public club structure leave it out.

Revision ID: e6040566c37c
Revises: c5d6e7f8a9b0
Create Date: 2026-10-19 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "e6040566c37c"
down_revision: Union[str, Sequence[str], None] = "c5d6e7f8a9b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("members", sa.Column("deleted_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    # Deleted accounts stay anonymized; without the column they are listed again, as "Deleted member".
    op.drop_column("members", "deleted_at")
