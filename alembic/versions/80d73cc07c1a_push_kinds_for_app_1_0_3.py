"""push: the member app 1.0.3's notification kinds

``push_notifications.kind`` gains eight values (docs/PUSH.md):
``event_updated``, ``event_cancelled``, ``event_live``,
``registration_closing``, ``certificate``, ``event_content``,
``weekly_summary`` and ``welcome``. Nothing else changes; no row is touched.

The downgrade deletes the rows of those kinds first (MySQL would otherwise
refuse, or blank, a value the narrower ENUM no longer has), then narrows the
column back.

Revision ID: 80d73cc07c1a
Revises: bfc96a1c1507
Create Date: 2026-10-23 14:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "80d73cc07c1a"
down_revision: Union[str, Sequence[str], None] = "bfc96a1c1507"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_KINDS = ("reminder", "accepted", "points", "new_event", "broadcast")
ADDED_KINDS = (
    "event_updated",
    "event_cancelled",
    "event_live",
    "registration_closing",
    "certificate",
    "event_content",
    "weekly_summary",
    "welcome",
)
NEW_KINDS = OLD_KINDS + ADDED_KINDS


def upgrade() -> None:
    op.alter_column(
        "push_notifications",
        "kind",
        existing_type=sa.Enum(*OLD_KINDS),
        type_=sa.Enum(*NEW_KINDS),
        existing_nullable=False,
    )


def downgrade() -> None:
    table = sa.table("push_notifications", sa.column("kind", sa.String))
    op.execute(table.delete().where(table.c.kind.in_(ADDED_KINDS)))
    op.alter_column(
        "push_notifications",
        "kind",
        existing_type=sa.Enum(*NEW_KINDS),
        type_=sa.Enum(*OLD_KINDS),
        existing_nullable=False,
    )
