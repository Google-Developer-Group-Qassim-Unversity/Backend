from sqlalchemy.orm import Session
from sqlalchemy import text


_MEMBERS_POINTS_BASE_QUERY = """
        WITH event_pts AS (
            SELECT
                ml.member_id AS member_id,
                SUM(COALESCE(a.points, 0) + COALESCE(mods.mod_value_sum, 0)) AS pts
            FROM members_logs ml
            JOIN logs l ON l.id = ml.log_id
            JOIN events e ON e.id = l.event_id
            LEFT JOIN actions a ON a.id = l.action_id
            LEFT JOIN (
                SELECT mo.log_id AS log_id,
                    SUM(CASE WHEN mo.type = 'discount' THEN -(ABS(COALESCE(mo.value, 0)))
                             WHEN mo.type = 'bonus' THEN ABS(COALESCE(mo.value, 0))
                             ELSE COALESCE(mo.value, 0) END) AS mod_value_sum
                FROM modifications mo
                GROUP BY mo.log_id
            ) mods ON mods.log_id = l.id
            WHERE e.semester_id = :semester_id AND e.status = 'published'
            GROUP BY ml.member_id
        ),
        bonus_pts AS (
            SELECT
                bgr.member_id AS member_id,
                SUM(bg.points) AS pts
            FROM bonus_grant_recipients bgr
            JOIN bonus_grants bg ON bg.id = bgr.bonus_grant_id
            JOIN bonuses b ON b.id = bg.bonus_id
            LEFT JOIN events e ON e.id = b.event_id
            WHERE bgr.member_id IS NOT NULL
              AND COALESCE(e.semester_id, b.semester_id) = :semester_id
              AND COALESCE(e.status, b.legacy_status) = 'published'
            GROUP BY bgr.member_id
        )
        SELECT
            m.id AS member_id,
            COALESCE(NULLIF(m.public_name, ''), 'Member') AS member_name,
            COALESCE(ep.pts, 0) + COALESCE(bp.pts, 0) AS total_points
        FROM members m
        LEFT JOIN event_pts ep ON ep.member_id = m.id
        LEFT JOIN bonus_pts bp ON bp.member_id = m.id
    """


def get_members_points_semester(session: Session, semester_id: str) -> list[dict]:
    """Every member's total points for the semester, ordered highest first. Deleted accounts are left out."""
    query = (
        _MEMBERS_POINTS_BASE_QUERY
        + """WHERE m.deleted_at IS NULL
        ORDER BY total_points DESC
    """
    )
    result = session.execute(text(query), {"semester_id": semester_id})
    return [dict(row._mapping) for row in result]


def get_member_points_by_id_semester(session: Session, semester_id: str, member_id: int) -> dict | None:
    """One member's total points for the semester, or None if they do not exist or deleted their account."""
    query = (
        _MEMBERS_POINTS_BASE_QUERY
        + """WHERE m.id = :member_id AND m.deleted_at IS NULL
    """
    )
    row = session.execute(text(query), {"semester_id": semester_id, "member_id": member_id}).first()
    return dict(row._mapping) if row else None


def get_member_points_history_semester(session: Session, semester_id: str, member_id: int) -> list[dict]:
    """Unified chronological history of published event attendance and public bonuses for one member."""
    statement = text("""
        WITH log_modifications AS (
            SELECT modifications.log_id AS log_id,
                SUM(CASE WHEN modifications.type = 'discount' THEN -(ABS(COALESCE(modifications.value, 0)))
                         WHEN modifications.type = 'bonus' THEN ABS(COALESCE(modifications.value, 0))
                         ELSE COALESCE(modifications.value, 0) END) AS mod_value_sum
            FROM modifications
            GROUP BY modifications.log_id
        ),
        event_rows AS (
            SELECT
                'event' AS kind,
                CAST(CONCAT('event:', e.id) AS CHAR(64)) COLLATE utf8mb4_0900_ai_ci AS id,
                e.name AS title,
                GROUP_CONCAT(DISTINCT a.action_name ORDER BY a.action_name ASC SEPARATOR ', ') AS label_en,
                GROUP_CONCAT(DISTINCT a.ar_action_name ORDER BY a.action_name ASC SEPARATOR ', ') AS label_ar,
                SUM(COALESCE(a.points, 0) + COALESCE(lm.mod_value_sum, 0)) AS points,
                e.start_datetime AS occurred_at,
                e.end_datetime AS end_datetime,
                e.id AS event_id,
                e.id AS legacy_event_id,
                CAST(NULL AS CHAR(36)) COLLATE utf8mb4_0900_ai_ci AS bonus_id,
                CAST(e.location_type AS CHAR(16)) COLLATE utf8mb4_0900_ai_ci AS location_type
            FROM members m
            JOIN members_logs ml ON m.id = ml.member_id
            JOIN logs l ON ml.log_id = l.id
            JOIN events e ON l.event_id = e.id
            JOIN actions a ON l.action_id = a.id
            LEFT JOIN log_modifications lm ON l.id = lm.log_id
            WHERE m.id = :member_id
                AND m.deleted_at IS NULL
                AND e.semester_id = :semester_id
                AND e.status = 'published'
            GROUP BY e.id, e.name, e.start_datetime, e.end_datetime, e.location_type
        ),
        bonus_rows AS (
            SELECT
                'bonus' AS kind,
                CONVERT(CONCAT('bonus:', bg.id) USING utf8mb4) COLLATE utf8mb4_0900_ai_ci AS id,
                COALESCE(e.name, b.title) AS title,
                bg.label_en AS label_en,
                bg.label_ar AS label_ar,
                bg.points AS points,
                COALESCE(e.start_datetime, b.given_at) AS occurred_at,
                COALESCE(e.end_datetime, b.legacy_end_datetime) AS end_datetime,
                b.event_id AS event_id,
                COALESCE(b.event_id, b.legacy_id) AS legacy_event_id,
                CONVERT(b.id USING utf8mb4) COLLATE utf8mb4_0900_ai_ci AS bonus_id,
                CAST(e.location_type AS CHAR(16)) COLLATE utf8mb4_0900_ai_ci AS location_type
            FROM members m
            JOIN bonus_grant_recipients bgr ON bgr.member_id = m.id
            JOIN bonus_grants bg ON bg.id = bgr.bonus_grant_id
            JOIN bonuses b ON b.id = bg.bonus_id
            LEFT JOIN events e ON e.id = b.event_id
            WHERE m.id = :member_id
                AND m.deleted_at IS NULL
                AND COALESCE(e.semester_id, b.semester_id) = :semester_id
                AND b.is_public = 1
                AND COALESCE(e.status, b.legacy_status) = 'published'
        )
        SELECT *
        FROM (
            SELECT * FROM event_rows
            UNION ALL
            SELECT * FROM bonus_rows
        ) combined
        ORDER BY occurred_at DESC, id DESC
    """)
    result = session.execute(statement, {"member_id": member_id, "semester_id": semester_id})
    return [dict(row._mapping) for row in result]


_DEPARTMENTS_POINTS_BASE_QUERY = """
        WITH event_pts AS (
            SELECT
                dl.department_id AS department_id,
                SUM((COALESCE(a.points, 0) + COALESCE(m.bonus, 0)) - COALESCE(m.discount, 0)) AS pts
            FROM departments_logs dl
            JOIN logs l ON l.id = dl.log_id
            JOIN events e ON e.id = l.event_id
            LEFT JOIN actions a ON a.id = l.action_id
            LEFT JOIN (
                SELECT modifications.log_id AS log_id,
                    SUM(CASE WHEN modifications.type = 'bonus' THEN COALESCE(modifications.value, 0) ELSE 0 END) AS bonus,
                    SUM(CASE WHEN modifications.type = 'discount' THEN COALESCE(modifications.value, 0) ELSE 0 END) AS discount
                FROM modifications
                GROUP BY modifications.log_id
            ) m ON m.log_id = l.id
            WHERE e.semester_id = :semester_id AND e.status = 'published'
            GROUP BY dl.department_id
        ),
        bonus_pts AS (
            SELECT
                bgr.department_id AS department_id,
                SUM(bg.points) AS pts
            FROM bonus_grant_recipients bgr
            JOIN bonus_grants bg ON bg.id = bgr.bonus_grant_id
            JOIN bonuses b ON b.id = bg.bonus_id
            LEFT JOIN events e ON e.id = b.event_id
            WHERE bgr.department_id IS NOT NULL
              AND COALESCE(e.semester_id, b.semester_id) = :semester_id
              AND COALESCE(e.status, b.legacy_status) = 'published'
            GROUP BY bgr.department_id
        )
        SELECT
            d.id AS department_id,
            COALESCE(sd.name, d.name) AS department_name,
            d.type AS department_type,
            COALESCE(sd.ar_name, d.ar_name) AS ar_department_name,
            COALESCE(ep.pts, 0) + COALESCE(bp.pts, 0) AS total_points
        FROM departments d
        LEFT JOIN semester_departments sd ON sd.department_id = d.id AND sd.semester_id = :semester_id
        LEFT JOIN event_pts ep ON ep.department_id = d.id
        LEFT JOIN bonus_pts bp ON bp.department_id = d.id
    """


def get_departments_points_semester(session: Session, semester_id: str) -> list[dict]:
    """The semester's departments and their total points, highest first.

    A department is listed when it is part of the semester (``semester_departments``)
    or earned points in it (via events or bonuses), so points are never hidden by
    a missing row. Departments with ``show_in_leaderboard = 0`` (the Board,
    Leadership) are left out. Names are the ones the department had that
    semester, when recorded.
    """
    query = (
        _DEPARTMENTS_POINTS_BASE_QUERY
        + """WHERE d.show_in_leaderboard = 1
          AND (sd.department_id IS NOT NULL OR ep.department_id IS NOT NULL OR bp.department_id IS NOT NULL)
        ORDER BY total_points DESC
    """
    )
    result = session.execute(text(query), {"semester_id": semester_id})
    return [dict(row._mapping) for row in result]


def get_department_points_by_id_semester(session: Session, semester_id: str, department_id: int) -> dict | None:
    """One department's total points for the semester, or None if it does not exist.

    Unlike the "all departments" query above, this is not limited to the
    semester's departments - an admin can look any department up by id.
    """
    query = (
        _DEPARTMENTS_POINTS_BASE_QUERY
        + """WHERE d.id = :department_id
    """
    )
    row = session.execute(text(query), {"semester_id": semester_id, "department_id": department_id}).first()
    return dict(row._mapping) if row else None


def get_department_points_history_semester(session: Session, semester_id: str, department_id: int) -> list[dict]:
    """Unified chronological history of published event actions and public bonuses for one department."""
    statement = text("""
        WITH log_modifications AS (
            SELECT mo.log_id AS log_id,
                SUM(CASE WHEN mo.type = 'discount' THEN -(ABS(COALESCE(mo.value, 0)))
                         WHEN mo.type = 'bonus' THEN ABS(COALESCE(mo.value, 0))
                         ELSE COALESCE(mo.value, 0) END) AS mod_value_sum
            FROM modifications mo
            GROUP BY mo.log_id
        ),
        event_rows AS (
            SELECT
                'event' AS kind,
                CAST(CONCAT('event:', e.id, ':', l.action_id) AS CHAR(64)) COLLATE utf8mb4_0900_ai_ci AS id,
                e.name AS title,
                a.action_name AS label_en,
                a.ar_action_name AS label_ar,
                SUM(COALESCE(a.points, 0) + COALESCE(lm.mod_value_sum, 0)) AS points,
                e.start_datetime AS occurred_at,
                e.end_datetime AS end_datetime,
                e.id AS event_id,
                e.id AS legacy_event_id,
                CAST(NULL AS CHAR(36)) COLLATE utf8mb4_0900_ai_ci AS bonus_id,
                CAST(e.location_type AS CHAR(16)) COLLATE utf8mb4_0900_ai_ci AS location_type
            FROM departments_logs dl
            JOIN logs l ON l.id = dl.log_id
            JOIN events e ON e.id = l.event_id
            JOIN actions a ON a.id = l.action_id
            LEFT JOIN log_modifications lm ON lm.log_id = l.id
            WHERE dl.department_id = :department_id
                AND e.semester_id = :semester_id
                AND e.status = 'published'
            GROUP BY e.id, e.name, e.start_datetime, e.end_datetime, e.location_type, l.action_id, a.action_name, a.ar_action_name
        ),
        bonus_rows AS (
            SELECT
                'bonus' AS kind,
                CONVERT(CONCAT('bonus:', bg.id) USING utf8mb4) COLLATE utf8mb4_0900_ai_ci AS id,
                COALESCE(e.name, b.title) AS title,
                bg.label_en AS label_en,
                bg.label_ar AS label_ar,
                bg.points AS points,
                COALESCE(e.start_datetime, b.given_at) AS occurred_at,
                COALESCE(e.end_datetime, b.legacy_end_datetime) AS end_datetime,
                b.event_id AS event_id,
                COALESCE(b.event_id, b.legacy_id) AS legacy_event_id,
                CONVERT(b.id USING utf8mb4) COLLATE utf8mb4_0900_ai_ci AS bonus_id,
                CAST(e.location_type AS CHAR(16)) COLLATE utf8mb4_0900_ai_ci AS location_type
            FROM bonus_grant_recipients bgr
            JOIN bonus_grants bg ON bg.id = bgr.bonus_grant_id
            JOIN bonuses b ON b.id = bg.bonus_id
            LEFT JOIN events e ON e.id = b.event_id
            WHERE bgr.department_id = :department_id
                AND COALESCE(e.semester_id, b.semester_id) = :semester_id
                AND b.is_public = 1
                AND COALESCE(e.status, b.legacy_status) = 'published'
        )
        SELECT *
        FROM (
            SELECT * FROM event_rows
            UNION ALL
            SELECT * FROM bonus_rows
        ) combined
        ORDER BY occurred_at DESC, id DESC
    """)
    result = session.execute(statement, {"department_id": department_id, "semester_id": semester_id})
    return [dict(row._mapping) for row in result]
