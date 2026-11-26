"""Add meeting_url_visibility policy column to events and open_events view.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-11-26 19:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, Sequence[str], None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

VISIBILITY_VALUES = ("public", "accepted", "hidden")


def _create_open_events_view(*, include_visibility: bool) -> None:
    op.execute("DROP VIEW IF EXISTS open_events")
    vis_col = "            e.meeting_url_visibility AS meeting_url_visibility,\n" if include_visibility else ""
    op.execute(f"""
        CREATE VIEW open_events AS
        SELECT
            e.id AS id,
            e.name AS name,
            e.description AS description,
            e.location_type AS location_type,
            e.location AS location,
            e.start_datetime AS start_datetime,
            e.end_datetime AS end_datetime,
            e.status AS status,
            e.level AS level,
            e.image_url AS image_url,
            e.meeting_url AS meeting_url,
{vis_col}            e.is_official AS is_official,
            e.semester_id AS semester_id,
            e.created_at AS created_at,
            e.published_at AS published_at,
            e.cancelled_at AS cancelled_at,
            f.id AS form_id,
            f.form_type AS form_type,
            f.google_responders_url AS google_responders_url,
            f.registration_status AS registration_status
        FROM events e
        JOIN forms f ON f.event_id = e.id
        WHERE e.status = 'published'
    """)


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column("meeting_url_visibility", sa.Enum(*VISIBILITY_VALUES), nullable=False, server_default="public"),
    )
    _create_open_events_view(include_visibility=True)


def downgrade() -> None:
    _create_open_events_view(include_visibility=False)
    op.drop_column("events", "meeting_url_visibility")
