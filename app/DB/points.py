from sqlalchemy.orm import Session
from app.DB.schema import t_members_points, t_member_event_history, t_departments_points, t_departments_points_history
from sqlalchemy import select, text


def get_all_members_points(session: Session):
    statement = select(t_members_points)
    members_points = session.execute(statement).all()
    return [dict(row._mapping) for row in members_points]

def get_member_points(session: Session, member_id: int):
    statement = select(t_members_points).where(t_members_points.c.member_id == member_id)
    member_points = session.execute(statement).first()
    if member_points is None:
        return None
    return dict(member_points._mapping)

def get_member_points_history(session: Session, member_id: int):
    statement = select(t_member_event_history).where(
        t_member_event_history.c.member_id == member_id,
        t_member_event_history.c.location_type != 'hidden'
    )
    member_points = session.execute(statement).all()
    return [dict(row._mapping) for row in member_points]

def get_all_departments_points(session: Session):
    statement = select(t_departments_points)
    departments_points = session.execute(statement).all()
    return [dict(row._mapping) for row in departments_points]

def get_department_points(session: Session, department_id: int):
    statement = select(t_departments_points).where(t_departments_points.c.department_id == department_id)
    department_points = session.execute(statement).first()
    if department_points is None:
        return None
    return dict(department_points._mapping)

def get_department_points_history(session: Session, department_id: int):
    statement = select(t_departments_points_history).where(
        t_departments_points_history.c.department_id == department_id,
        t_departments_points_history.c.location_type != 'hidden'
    )
    department_points = session.execute(statement).all()
    return [dict(row._mapping) for row in department_points]

# =============================================================================
# New Parameterized Queries (above queries are still used for performance mesaurement, they can be removed later)
# these are an exact* copy of the actual 4 points views in the database, and where mesaured and showed same or faster performance than the above ORM ones) 
# =============================================================================

def get_members_points_semester(session: Session, start_date: str, end_date: str, member_id: int | None = None):
    params: dict = {"start_date": start_date, "end_date": end_date}
    if member_id:
        params["member_id"] = member_id

    query = """
        SELECT
            m.id AS member_id,
            m.name AS member_name,
            COALESCE(SUM(CASE WHEN e.id IS NOT NULL THEN (COALESCE(a.points, 0) + COALESCE(mods.mod_value_sum, 0)) ELSE 0 END), 0) AS total_points
        FROM members m
        LEFT JOIN members_logs ml ON ml.member_id = m.id
        LEFT JOIN logs l ON l.id = ml.log_id
        LEFT JOIN events e ON e.id = l.event_id AND e.end_datetime > :start_date AND e.end_datetime < :end_date AND e.status <> 'draft'
        LEFT JOIN actions a ON a.id = l.action_id
        LEFT JOIN (
            SELECT mo.log_id AS log_id,
                SUM(CASE WHEN mo.type = 'discount' THEN -(ABS(COALESCE(mo.value, 0)))
                         WHEN mo.type = 'bonus' THEN ABS(COALESCE(mo.value, 0))
                         ELSE COALESCE(mo.value, 0) END) AS mod_value_sum
            FROM modifications mo
            GROUP BY mo.log_id
        ) mods ON mods.log_id = l.id
    """ + ("WHERE m.id = :member_id\n    " if member_id else "") + """GROUP BY m.id, m.name
        ORDER BY total_points DESC
    """
    result = session.execute(text(query), params)

    if member_id:
        row = result.first()
        return dict(row._mapping) if row else None
    return [dict(row._mapping) for row in result]


def get_member_points_history_semester(session: Session, member_id: int, start_date: str, end_date: str):
    statement = text("""
        WITH log_modifications AS (
            SELECT modifications.log_id AS log_id,
                SUM(CASE WHEN modifications.type = 'discount' THEN -(ABS(COALESCE(modifications.value, 0)))
                         WHEN modifications.type = 'bonus' THEN ABS(COALESCE(modifications.value, 0))
                         ELSE COALESCE(modifications.value, 0) END) AS mod_value_sum
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
            SUM((COALESCE(a.points, 0) + COALESCE(lm.mod_value_sum, 0))) AS points,
            GROUP_CONCAT(DISTINCT a.action_name ORDER BY a.action_name ASC SEPARATOR ', ') AS action_name,
            GROUP_CONCAT(DISTINCT a.ar_action_name ORDER BY a.action_name ASC SEPARATOR ', ') AS ar_action_name
        FROM members m
        JOIN members_logs ml ON m.id = ml.member_id
        JOIN logs l ON ml.log_id = l.id
        JOIN events e ON l.event_id = e.id
        JOIN actions a ON l.action_id = a.id
        LEFT JOIN log_modifications lm ON l.id = lm.log_id
        WHERE m.id = :member_id
            AND e.end_datetime > :start_date
            AND e.end_datetime < :end_date
            AND e.status <> 'draft'
            AND e.location_type <> 'hidden'
        GROUP BY m.id, m.name, e.id, e.name, e.start_datetime, e.end_datetime
        ORDER BY e.start_datetime DESC
    """)
    result = session.execute(statement, {"member_id": member_id, "start_date": start_date, "end_date": end_date})
    return [dict(row._mapping) for row in result]


def get_departments_points_semester(session: Session, start_date: str, end_date: str, department_id: int | None = None):
    params: dict = {"start_date": start_date, "end_date": end_date}
    if department_id:
        params["department_id"] = department_id

    query = """
        SELECT
            d.id AS department_id,
            d.name AS department_name,
            d.type AS department_type,
            d.ar_name AS ar_department_name,
            COALESCE(SUM(CASE WHEN e.id IS NOT NULL THEN ((COALESCE(a.points, 0) + COALESCE(m.bonus, 0)) - COALESCE(m.discount, 0)) ELSE 0 END), 0) AS total_points
        FROM departments d
        LEFT JOIN departments_logs dl ON dl.department_id = d.id
        LEFT JOIN logs l ON l.id = dl.log_id
        LEFT JOIN events e ON e.id = l.event_id AND e.end_datetime > :start_date AND e.end_datetime < :end_date AND e.status <> 'draft'
        LEFT JOIN actions a ON a.id = l.action_id
        LEFT JOIN (
            SELECT modifications.log_id AS log_id,
                SUM(CASE WHEN modifications.type = 'bonus' THEN COALESCE(modifications.value, 0) ELSE 0 END) AS bonus,
                SUM(CASE WHEN modifications.type = 'discount' THEN COALESCE(modifications.value, 0) ELSE 0 END) AS discount
            FROM modifications
            GROUP BY modifications.log_id
        ) m ON m.log_id = l.id
    """ + ("WHERE d.id = :department_id\n    " if department_id else "") + """GROUP BY d.id, d.name, d.type, d.ar_name
        ORDER BY total_points DESC
    """
    result = session.execute(text(query), params)

    if department_id:
        row = result.first()
        return dict(row._mapping) if row else None
    return [dict(row._mapping) for row in result]


def get_department_points_history_semester(session: Session, department_id: int, start_date: str, end_date: str):
    statement = text("""
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
            SUM((COALESCE(a.points, 0) + COALESCE(mods.mod_value_sum, 0))) AS points
        FROM departments_logs dl
        JOIN departments d ON d.id = dl.department_id
        JOIN logs l ON l.id = dl.log_id
        JOIN events e ON e.id = l.event_id
        JOIN actions a ON a.id = l.action_id
        LEFT JOIN (
            SELECT mo.log_id AS log_id,
                SUM(CASE WHEN mo.type = 'discount' THEN -(ABS(COALESCE(mo.value, 0)))
                         WHEN mo.type = 'bonus' THEN ABS(COALESCE(mo.value, 0))
                         ELSE COALESCE(mo.value, 0) END) AS mod_value_sum
            FROM modifications mo
            GROUP BY mo.log_id
        ) mods ON mods.log_id = l.id
        WHERE dl.department_id = :department_id
            AND e.end_datetime > :start_date
            AND e.end_datetime < :end_date
            AND e.status <> 'draft'
            AND e.location_type <> 'hidden'
        GROUP BY dl.department_id, d.name, d.ar_name, l.event_id, e.name, e.start_datetime, e.end_datetime, l.action_id, a.action_name, a.ar_action_name
        ORDER BY e.start_datetime DESC
    """)
    result = session.execute(statement, {"department_id": department_id, "start_date": start_date, "end_date": end_date})
    return [dict(row._mapping) for row in result]