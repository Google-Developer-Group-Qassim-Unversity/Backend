"""Replace 4-value events.status with publication status + form registration lifecycle.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-11-26 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_EVENT_STATUS = ("draft", "open", "active", "closed")
EXPANDED_EVENT_STATUS = ("draft", "open", "active", "closed", "published", "cancelled")
NEW_EVENT_STATUS = ("draft", "published", "cancelled")

OLD_HISTORY_ACTIONS = (
    "created",
    "edited",
    "status_changed",
    "meeting_url_changed",
    "content_link_changed",
    "deleted",
    "attendance_marked",
    "attendance_scanned",
    "attendance_backfilled",
    "attendance_removed",
    "submissions_reviewed",
    "form_updated",
    "form_attached",
    "form_detached",
)

NEW_HISTORY_ACTIONS = (
    "created",
    "edited",
    "status_changed",
    "registration_changed",
    "meeting_url_changed",
    "content_link_changed",
    "deleted",
    "attendance_marked",
    "attendance_scanned",
    "attendance_backfilled",
    "attendance_removed",
    "submissions_reviewed",
    "form_updated",
    "form_attached",
    "form_detached",
)


def _create_new_open_events_view() -> None:
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
            e.level AS level,
            e.image_url AS image_url,
            e.meeting_url AS meeting_url,
            e.is_official AS is_official,
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


def _create_legacy_open_events_view() -> None:
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
            e.level AS level,
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
    op.execute("DROP VIEW IF EXISTS open_events")

    # 1. Add lifecycle timestamps on events.
    op.add_column("events", sa.Column("published_at", sa.DateTime(), nullable=True))
    op.add_column("events", sa.Column("cancelled_at", sa.DateTime(), nullable=True))

    # 2. Add registration status column on forms.
    op.add_column(
        "forms",
        sa.Column(
            "registration_status", sa.Enum("not_open", "open", "closed"), nullable=False, server_default="not_open"
        ),
    )

    # 3. Add 'registration_changed' to event_history.action enum.
    op.alter_column(
        "event_history",
        "action",
        existing_type=sa.Enum(*OLD_HISTORY_ACTIONS, name="eventhistoryaction"),
        type_=sa.Enum(*NEW_HISTORY_ACTIONS, name="eventhistoryaction"),
        nullable=False,
    )

    # 4. Expand events.status enum to hold both old and new values during backfill.
    op.alter_column(
        "events",
        "status",
        existing_type=sa.Enum(*OLD_EVENT_STATUS),
        type_=sa.Enum(*EXPANDED_EVENT_STATUS),
        nullable=False,
    )

    # 5. Backfill forms.registration_status BEFORE updating events.status.
    op.execute("""
        UPDATE forms f
        JOIN events e ON e.id = f.event_id
        SET f.registration_status = CASE
            WHEN f.form_type = 'none' THEN 'not_open'
            WHEN e.status = 'open' THEN 'open'
            WHEN e.status = 'draft' THEN 'not_open'
            WHEN e.status IN ('active', 'closed') THEN 'closed'
            ELSE 'not_open'
        END
    """)

    # 6. Backfill events.status and events.published_at.
    op.execute("""
        UPDATE events
        SET status = 'published',
            published_at = created_at
        WHERE status IN ('open', 'active', 'closed')
    """)

    # 7. Shrink events.status enum to the final 3 values.
    op.alter_column(
        "events",
        "status",
        existing_type=sa.Enum(*EXPANDED_EVENT_STATUS),
        type_=sa.Enum(*NEW_EVENT_STATUS),
        nullable=False,
    )

    # 8. Rebuild open_events SQL view.
    _create_new_open_events_view()


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS open_events")

    # 1. Expand events.status enum so legacy values can be restored.
    op.alter_column(
        "events",
        "status",
        existing_type=sa.Enum(*NEW_EVENT_STATUS),
        type_=sa.Enum(*EXPANDED_EVENT_STATUS),
        nullable=False,
    )

    # 2. Map published/cancelled rows back to legacy status values.
    op.execute("""
        UPDATE events e
        LEFT JOIN forms f ON f.event_id = e.id
        SET e.status = CASE
            WHEN e.status = 'cancelled' THEN 'closed'
            WHEN f.registration_status = 'open' THEN 'open'
            WHEN f.registration_status = 'closed' AND e.end_datetime >= NOW() THEN 'active'
            WHEN e.end_datetime < NOW() THEN 'closed'
            WHEN e.start_datetime <= NOW() THEN 'active'
            ELSE 'open'
        END
        WHERE e.status IN ('published', 'cancelled')
    """)

    # 3. Shrink events.status enum back to legacy 4 values.
    op.alter_column(
        "events",
        "status",
        existing_type=sa.Enum(*EXPANDED_EVENT_STATUS),
        type_=sa.Enum(*OLD_EVENT_STATUS),
        nullable=False,
    )

    # 4. Remove registration_changed history rows and restore event_history.action enum.
    op.execute("DELETE FROM event_history WHERE action = 'registration_changed'")
    op.alter_column(
        "event_history",
        "action",
        existing_type=sa.Enum(*NEW_HISTORY_ACTIONS, name="eventhistoryaction"),
        type_=sa.Enum(*OLD_HISTORY_ACTIONS, name="eventhistoryaction"),
        nullable=False,
    )

    # 5. Drop added columns on forms and events.
    op.drop_column("forms", "registration_status")
    op.drop_column("events", "cancelled_at")
    op.drop_column("events", "published_at")

    # 6. Restore legacy open_events SQL view.
    _create_legacy_open_events_view()
