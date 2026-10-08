"""soft-delete submissions: cancelled_at

Revision ID: a2d4f6b8c0e1
Revises: 9f7a8b9c0d1e
Create Date: 2026-10-06 00:00:00.000000

A member cancelling their registration used to delete the row. Now it is kept
and stamped with ``cancelled_at``, so the club still knows who registered and
who changed their mind. The forms_submissions view hides cancelled rows, which
keeps them out of everything built on it: the admin registrations list,
acceptance emails and the Google Forms partial sync.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "a2d4f6b8c0e1"
down_revision: Union[str, Sequence[str], None] = "9f7a8b9c0d1e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _create_forms_submissions_view(hide_cancelled: bool) -> None:
    where = "WHERE s.cancelled_at IS NULL" if hide_cancelled else ""
    op.execute("DROP VIEW IF EXISTS forms_submissions")
    op.execute(f"""
        CREATE VIEW forms_submissions AS
        SELECT
            s.id AS submission_id,
            s.submitted_at AS submitted_at,
            f.form_type AS form_type,
            s.submission_type AS submission_type,
            m.id AS id,
            m.name AS name,
            m.email AS email,
            m.phone_number AS phone_number,
            m.uni_id AS uni_id,
            m.gender AS gender,
            m.uni_level AS uni_level,
            m.uni_college AS uni_college,
            s.is_accepted AS is_accepted,
            s.is_invited AS is_invited,
            s.google_submission_value AS google_submission_value,
            f.event_id AS event_id,
            f.id AS form_id,
            f.google_form_id AS google_form_id
        FROM submissions s
        JOIN forms f ON s.form_id = f.id
        JOIN members m ON s.member_id = m.id
        {where}
    """)


def upgrade() -> None:
    op.add_column("submissions", sa.Column("cancelled_at", sa.DateTime(), nullable=True))
    _create_forms_submissions_view(hide_cancelled=True)


def downgrade() -> None:
    # Before this revision a cancellation was a delete, so that is what the kept rows become.
    op.execute("DELETE FROM submissions WHERE cancelled_at IS NOT NULL")
    _create_forms_submissions_view(hide_cancelled=False)
    op.drop_column("submissions", "cancelled_at")
