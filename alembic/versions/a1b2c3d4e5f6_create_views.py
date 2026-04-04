"""create views

Revision ID: a1b2c3d4e5f6
Revises: 4f7ac1426f31
Create Date: 2026-03-28 17:00:00.000000

"""

from typing import Sequence, Union

from alembic import op


revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "4f7ac1426f31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Views releated to points have been deprecated
# instead they hvae been defined in-code in the /DB/points file to allow for parameterization of the date range.

# they are kept for 2 reasons:
#   1. reference given they are a core part of the logic.
#   2. they are used for performance measurement against the new parameterized queries


def upgrade() -> None:
    op.execute("""
        CREATE VIEW members_points AS
        SELECT
            m.id AS member_id,
            m.name AS member_name,
            COALESCE(SUM(CASE WHEN e.id IS NOT NULL THEN (COALESCE(a.points, 0) + COALESCE(mods.mod_value_sum, 0)) ELSE 0 END), 0) AS total_points
        FROM members m
        LEFT JOIN members_logs ml ON ml.member_id = m.id
        LEFT JOIN logs l ON l.id = ml.log_id
        LEFT JOIN events e ON e.id = l.event_id
            AND e.end_datetime > '2026-01-18'
            AND e.end_datetime < '2026-08-23'
            AND e.status <> 'draft'
        LEFT JOIN actions a ON a.id = l.action_id
        LEFT JOIN (
            SELECT mo.log_id AS log_id,
                   SUM(CASE WHEN mo.type = 'discount' THEN -ABS(COALESCE(mo.value, 0))
                            WHEN mo.type = 'bonus' THEN ABS(COALESCE(mo.value, 0))
                            ELSE COALESCE(mo.value, 0)
                       END) AS mod_value_sum
            FROM modifications mo
            GROUP BY mo.log_id
        ) mods ON mods.log_id = l.id
        GROUP BY m.id, m.name
        ORDER BY total_points DESC
    """)

    op.execute("""
        CREATE VIEW departments_points AS
        SELECT
            d.id AS department_id,
            d.name AS department_name,
            d.type AS department_type,
            d.ar_name AS ar_department_name,
            COALESCE(SUM(CASE WHEN e.id IS NOT NULL THEN ((COALESCE(a.points, 0) + COALESCE(m.bonus, 0)) - COALESCE(m.discount, 0)) ELSE 0 END), 0) AS total_points
        FROM departments d
        LEFT JOIN departments_logs dl ON dl.department_id = d.id
        LEFT JOIN logs l ON l.id = dl.log_id
        LEFT JOIN events e ON e.id = l.event_id
            AND e.end_datetime > '2026-01-18'
            AND e.end_datetime < '2026-08-23'
            AND e.status <> 'draft'
        LEFT JOIN actions a ON a.id = l.action_id
        LEFT JOIN (
            SELECT modifications.log_id AS log_id,
                   SUM(CASE WHEN modifications.type = 'bonus' THEN COALESCE(modifications.value, 0) ELSE 0 END) AS bonus,
                   SUM(CASE WHEN modifications.type = 'discount' THEN COALESCE(modifications.value, 0) ELSE 0 END) AS discount
            FROM modifications
            GROUP BY modifications.log_id
        ) m ON m.log_id = l.id
        GROUP BY d.id, d.name, d.type, d.ar_name
        ORDER BY total_points DESC
    """)

    op.execute("""
        CREATE VIEW departments_points_history AS
        SELECT
            dl.department_id AS department_id,
            d.name AS department_name,
            d.ar_name AS ar_department_name,
            l.event_id AS event_id,
            e.name AS event_name,
            e.start_datetime AS start_datetime,
            e.end_datetime AS end_datetime,
            e.status AS status,
            a.action_name AS action_name,
            a.ar_action_name AS ar_action_name,
            e.location_type AS location_type,
            SUM(COALESCE(a.points, 0) + COALESCE(mods.mod_value_sum, 0)) AS points
        FROM departments_logs dl
        JOIN departments d ON d.id = dl.department_id
        JOIN logs l ON l.id = dl.log_id
        JOIN events e ON e.id = l.event_id
        JOIN actions a ON a.id = l.action_id
        LEFT JOIN (
            SELECT mo.log_id AS log_id,
                   SUM(CASE WHEN mo.type = 'discount' THEN -ABS(COALESCE(mo.value, 0))
                            WHEN mo.type = 'bonus' THEN ABS(COALESCE(mo.value, 0))
                            ELSE COALESCE(mo.value, 0)
                       END) AS mod_value_sum
            FROM modifications mo
            GROUP BY mo.log_id
        ) mods ON mods.log_id = l.id
        WHERE e.end_datetime > '2026-01-18'
          AND e.end_datetime < '2026-08-23'
          AND e.status <> 'draft'
        GROUP BY dl.department_id, d.name, d.ar_name, l.event_id, e.name, e.start_datetime, e.end_datetime, l.action_id, a.action_name, a.ar_action_name
        ORDER BY e.start_datetime DESC
    """)

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

    op.execute("""
        CREATE VIEW members_points_history AS
        WITH log_modifications AS (
            SELECT modifications.log_id AS log_id,
                   SUM(CASE WHEN modifications.type = 'discount' THEN -ABS(COALESCE(modifications.value, 0))
                            WHEN modifications.type = 'bonus' THEN ABS(COALESCE(modifications.value, 0))
                            ELSE COALESCE(modifications.value, 0)
                       END) AS mod_value_sum
            FROM modifications
            GROUP BY modifications.log_id
        )
        SELECT
            m.id AS member_id,
            m.name AS member_name,
            e.id AS event_id,
            e.name AS event_name,
            e.start_datetime AS start_datetime,
            e.end_datetime AS end_datetime,
            e.location_type AS location_type,
            SUM(COALESCE(a.points, 0) + COALESCE(lm.mod_value_sum, 0)) AS points,
            GROUP_CONCAT(DISTINCT a.action_name ORDER BY a.action_name ASC SEPARATOR ', ') AS action_name,
            GROUP_CONCAT(DISTINCT a.ar_action_name ORDER BY a.action_name ASC SEPARATOR ', ') AS ar_action_name
        FROM members m
        JOIN members_logs ml ON m.id = ml.member_id
        JOIN logs l ON ml.log_id = l.id
        JOIN events e ON l.event_id = e.id
        JOIN actions a ON l.action_id = a.id
        LEFT JOIN log_modifications lm ON l.id = lm.log_id
        WHERE e.end_datetime > '2026-01-18'
          AND e.end_datetime < '2026-08-23'
          AND e.status <> 'draft'
        GROUP BY m.id, m.name, e.id, e.name, e.start_datetime, e.end_datetime
        ORDER BY e.start_datetime DESC
    """)

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


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS open_events")
    op.execute("DROP VIEW IF EXISTS member_event_history")
    op.execute("DROP VIEW IF EXISTS forms_submissions")
    op.execute("DROP VIEW IF EXISTS departments_points_history")
    op.execute("DROP VIEW IF EXISTS departments_points")
    op.execute("DROP VIEW IF EXISTS members_points")
