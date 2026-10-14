"""Design's Notion card

``event_request_tasks`` keeps, on Design's row, the page the backend writes on
the design team's Notion board: its id and URL, whether a change still has to
be pushed, when it last was, and the last error if the push failed
(``app/services/pipeline_notion.py``).

Revision ID: a7c2e9d4b1f3
Revises: 267cb3fe563f
Create Date: 2026-10-14 09:10:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision: str = "a7c2e9d4b1f3"
down_revision: Union[str, Sequence[str], None] = "267cb3fe563f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("event_request_tasks", sa.Column("notion_page_id", sa.String(36), nullable=True))
    op.add_column("event_request_tasks", sa.Column("notion_page_url", sa.String(255), nullable=True))
    op.add_column(
        "event_request_tasks",
        sa.Column("notion_sync_pending", mysql.TINYINT(1), nullable=False, server_default=sa.text("'0'")),
    )
    op.add_column("event_request_tasks", sa.Column("notion_synced_at", sa.DateTime(), nullable=True))
    op.add_column(
        "event_request_tasks",
        sa.Column("notion_error", mysql.TEXT(charset="utf8mb4", collation="utf8mb4_0900_ai_ci"), nullable=True),
    )


def downgrade() -> None:
    for column in ("notion_error", "notion_synced_at", "notion_sync_pending", "notion_page_url", "notion_page_id"):
        op.drop_column("event_request_tasks", column)
