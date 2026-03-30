"""add is_invited to submissions

Revision ID: b2c3d4e5f6g7
Revises: a1b2c3d4e5f6
Create Date: 2026-03-29 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = 'b2c3d4e5f6g7'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'submissions',
        sa.Column(
            'is_invited',
            mysql.TINYINT(1),
            nullable=False,
            server_default=sa.text("'0'")
        )
    )

    op.execute("DROP VIEW IF EXISTS forms_submissions")

    op.execute("""
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
    """)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS forms_submissions")

    op.execute("""
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
            s.google_submission_value AS google_submission_value,
            f.event_id AS event_id,
            f.id AS form_id,
            f.google_form_id AS google_form_id
        FROM submissions s
        JOIN forms f ON s.form_id = f.id
        JOIN members m ON s.member_id = m.id
    """)

    op.drop_column('submissions', 'is_invited')