"""push: the member app 1.0.3's notification kinds, and when content was published

- ``push_notifications.kind`` gains eight values (docs/PUSH.md):
  ``event_updated``, ``event_cancelled``, ``event_live``,
  ``registration_closing``, ``certificate``, ``event_content``,
  ``weekly_summary`` and ``welcome``.
- ``event_content_links.published_at`` (UTC, NULL while a draft): when a link
  last became visible to members. ``event_content`` pushes reach members whose
  attendance is recorded within 7 days of it. Links published already get
  their ``created_at``, the best that is known about them.

The downgrade drops the column, deletes the rows of the new kinds (MySQL
would otherwise refuse, or blank, a value the narrower ENUM no longer has),
then narrows the column back.

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
    op.add_column("event_content_links", sa.Column("published_at", sa.DateTime(), nullable=True))
    op.execute("UPDATE event_content_links SET published_at = created_at WHERE published = 1")


def downgrade() -> None:
    op.drop_column("event_content_links", "published_at")
    table = sa.table("push_notifications", sa.column("kind", sa.String))
    op.execute(table.delete().where(table.c.kind.in_(ADDED_KINDS)))
    op.alter_column(
        "push_notifications",
        "kind",
        existing_type=sa.Enum(*NEW_KINDS),
        type_=sa.Enum(*OLD_KINDS),
        existing_nullable=False,
    )
