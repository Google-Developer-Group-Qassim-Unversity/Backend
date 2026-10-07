"""add level to events

Revision ID: b3e7c1d9a5f2
Revises: a2d4f6b8c0e1
Create Date: 2026-10-06 00:00:00.000000

Every event gets a level (beginner / intermediate / advanced). Existing events
become beginner through the server default, so the column is NOT NULL from the
start. The open_events view is rebuilt to carry it, since the member app reads
its event cards from there.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "b3e7c1d9a5f2"
down_revision: Union[str, Sequence[str], None] = "a2d4f6b8c0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _create_open_events_view(with_level: bool) -> None:
    level = "e.level AS level," if with_level else ""
    op.execute("DROP VIEW IF EXISTS open_events")
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
            {level}
            e.image_url AS image_url,
            e.meeting_url AS meeting_url,
            e.is_official AS is_official,
            f.id AS form_id,
            f.form_type AS form_type,
            f.google_responders_url AS google_responders_url
        FROM events e
        JOIN forms f ON f.event_id = e.id
        WHERE e.status = 'open'
           OR (e.status = 'active' AND e.location_type = 'online' AND f.form_type = 'none')
    """)


def upgrade() -> None:
    op.add_column(
        "events",
        sa.Column("level", sa.Enum("beginner", "intermediate", "advanced"), nullable=False, server_default="beginner"),
    )
    _create_open_events_view(with_level=True)


def downgrade() -> None:
    _create_open_events_view(with_level=False)
    op.drop_column("events", "level")
