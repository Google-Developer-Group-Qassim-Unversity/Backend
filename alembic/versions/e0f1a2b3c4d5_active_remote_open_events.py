"""include active remote no-registration events in open_events view

Revision ID: e0f1a2b3c4d5
Revises: d9e0f1a2b3c4
Create Date: 2026-09-02 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


revision: str = "e0f1a2b3c4d5"
down_revision: Union[str, Sequence[str], None] = "d9e0f1a2b3c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP VIEW IF EXISTS open_events")

    op.execute("""
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


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS open_events")

    op.execute("""
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
            e.image_url AS image_url,
            e.is_official AS is_official,
            f.id AS form_id,
            f.form_type AS form_type,
            f.google_responders_url AS google_responders_url
        FROM events e
        JOIN forms f ON f.event_id = e.id
        WHERE e.status = 'open'
    """)
