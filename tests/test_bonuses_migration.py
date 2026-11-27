"""Migration test for a7b8c9d0e1f2: fake events, real-event custom logs, penalties, and pushes."""

from datetime import datetime
import json

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.DB import points

from app.DB.main import _build_connect_args
from tests.conftest import _drill_server_root

BEFORE = "f6a7b8c9d0e1"
AFTER = "a7b8c9d0e1f2"


def _legacy_totals(conn):
    totals = {}
    for recipient in ("member", "department"):
        rows = conn.execute(
            text(f"""
            SELECT e.semester_id, r.{recipient}_id,
                SUM(a.points + COALESCE(m.delta, 0)) AS total
            FROM {recipient}s_logs r
            JOIN logs l ON l.id = r.log_id
            JOIN actions a ON a.id = l.action_id
            JOIN events e ON e.id = l.event_id
            LEFT JOIN (
                SELECT log_id, SUM(CASE WHEN type = 'discount' THEN -CAST(value AS SIGNED)
                                       ELSE CAST(value AS SIGNED) END) AS delta
                FROM modifications GROUP BY log_id
            ) m ON m.log_id = l.id
            WHERE e.status = 'published'
            GROUP BY e.semester_id, r.{recipient}_id
        """)
        )
        for semester_id, recipient_id, total in rows:
            totals[recipient, semester_id, recipient_id] = int(total)
    return totals


def _current_totals(conn):
    totals = {}
    semester_ids = conn.execute(text("SELECT id FROM semesters")).scalars().all()
    with Session(bind=conn) as session:
        for semester_id in semester_ids:
            members = points.get_members_points_semester(session, semester_id)
            departments = points.get_departments_points_semester(session, semester_id)
            for recipient, rows in (("member", members), ("department", departments)):
                for row in rows:
                    total = int(row["total_points"])
                    if total:
                        totals[recipient, semester_id, row[f"{recipient}_id"]] = total
    return totals


def test_bonuses_migration_preserves_points_pushes_and_penalties(downgrade_drill_database_url, monkeypatch):
    # Use a dedicated scratch database so we never collide with sibling migration tests.
    root_url = _drill_server_root["server_url"]
    scratch_db = "gdg_bonuses_migration_drill"
    root_engine = create_engine(root_url, connect_args=_build_connect_args(root_url))
    with root_engine.begin() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS `{scratch_db}`"))
        conn.execute(text(f"CREATE DATABASE `{scratch_db}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"))

    url = make_url(root_url).set(database=scratch_db).render_as_string(hide_password=False)
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, BEFORE)
    engine = create_engine(url, connect_args=_build_connect_args(url))
    try:
        with engine.begin() as conn:
            sem_id, next_semester = (
                conn.execute(text("SELECT id FROM semesters ORDER BY start_date LIMIT 2")).scalars().all()
            )
            conn.execute(text("INSERT INTO members (id, name, public_name, gender) VALUES (91, 'Ali', 'Ali', 'Male')"))
            conn.execute(
                text("INSERT INTO departments (id, name, type, ar_name) VALUES (91, 'Tech', 'practical', 'التقنية')")
            )
            conn.execute(
                text(
                    "INSERT INTO actions (id, action_name, points, action_type, ar_action_name) VALUES "
                    "(51, 'Organized On-site', 10, 'composite', 'تنظيم حضوري'), "
                    "(76, 'On-site attendance', 6, 'member', 'حضور حضوري'), "
                    "(108, 'Tournament participation', 5, 'bonus', 'مشاركة في بطولة'), "
                    "(200, 'Bonus', 0, 'bonus', 'مكافأة'), "
                    "(201, 'Discount', 0, 'bonus', 'خصم'), "
                    "(202, 'Best Speaker', 8, 'bonus', 'أفضل متحدث')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO events (id, name, location_type, location, start_datetime, end_datetime, status, semester_id) VALUES "
                    "(10, 'Public Custom', 'none', 'none', '2026-07-01 10:00:00', '2026-07-01 12:00:00', 'published', :sem), "
                    "(11, 'Hidden Custom', 'hidden', 'none', '2026-07-02 10:00:00', '2026-07-02 12:00:00', 'published', :sem), "
                    "(12, 'Real Workshop', 'on-site', 'Hall', '2026-07-03 10:00:00', '2026-07-03 12:00:00', 'published', :sem), "
                    "(13, 'Esports Cup', 'on-site', 'Hall', '2026-07-04 10:00:00', '2026-07-04 12:00:00', 'published', :later), "
                    "(14, 'Cancelled Custom', 'none', 'none', '2026-07-05 10:00:00', '2026-07-05 12:00:00', 'cancelled', :later)"
                ),
                {"sem": sem_id, "later": next_semester},
            )
            conn.execute(
                text(
                    "INSERT INTO logs (id, action_id, event_id) VALUES "
                    "(100, 200, 10), "
                    "(101, 201, 11), "
                    "(102, 51, 12), "
                    "(103, 76, 12), "
                    "(104, 202, 12), "
                    "(105, 201, 12), "
                    "(106, 51, 13), "
                    "(107, 108, 13), (108, 202, 14)"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO modifications (log_id, type, value) VALUES "
                    "(100, 'bonus', 7), (101, 'discount', 3), (105, 'discount', 2)"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO members_logs (member_id, log_id, date) VALUES "
                    "(91, 100, '2026-07-01 11:00:00'), "
                    "(91, 100, '2026-07-01 11:30:00'), "
                    "(91, 101, '2026-07-02 11:00:00'), "
                    "(91, 103, '2026-07-03 11:00:00'), "
                    "(91, 104, '2026-07-03 11:30:00'), "
                    "(91, 105, '2026-07-03 11:30:00'), "
                    "(91, 107, '2026-07-04 11:00:00'), "
                    "(91, 108, '2026-07-05 11:00:00')"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO departments_logs (department_id, log_id) VALUES (91, 102), (91, 102), (91, 105), (91, 106), (91, 108)"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO event_requests (id, department_id, stage, requested_by, event_id) "
                    "VALUES ('00000000-0000-7000-8000-000000000001', 91, 'published', 91, 12)"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO pipeline_penalties (id, request_id, department_id, late_days, points, reason, applied_log_id) "
                    "VALUES ('00000000-0000-7000-8000-000000000002', '00000000-0000-7000-8000-000000000001', 91, 1, 2, 'Late', 105)"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO push_notifications (id, member_id, kind, dedupe_key, event_id, payload, status, not_before, expires_at) "
                    "VALUES ('00000000-0000-7000-8000-000000000003', 91, 'points', 'points:custom:100:91', 10, "
                    "JSON_OBJECT('cause', JSON_OBJECT('type', 'custom', 'log_id', 100)), 'sent', :dt, :dt), "
                    "('00000000-0000-7000-8000-000000000004', 91, 'points', 'points:custom:104:91', 12, "
                    "JSON_OBJECT('cause', JSON_OBJECT('type', 'custom', 'log_id', 104)), 'pending', :dt, :dt)"
                ),
                {"dt": datetime(2026, 7, 1, 11, 0)},
            )

            before = _legacy_totals(conn)
            assert before == {
                ("member", sem_id, 91): 23,
                ("member", next_semester, 91): 5,
                ("department", sem_id, 91): 18,
                ("department", next_semester, 91): 10,
            }

        command.upgrade(config, AFTER)

        with engine.connect() as conn:
            assert _current_totals(conn) == before
            event_ids = conn.execute(text("SELECT id FROM events ORDER BY id")).scalars().all()
            assert event_ids == [12, 13]

            push = conn.execute(
                text("SELECT event_id, bonus_id, dedupe_key, payload FROM push_notifications ORDER BY id")
            ).all()
            assert [row.event_id for row in push] == [None, 12]
            for row in push:
                cause = json.loads(row.payload)["cause"]
                assert cause["type"] == "bonus"
                assert cause["bonus_id"] == row.bonus_id
                assert row.dedupe_key == f"points:bonus:{cause['grant_id']}:91"

            bonuses = conn.execute(text("SELECT title, event_id, is_public FROM bonuses ORDER BY title")).all()
            assert bonuses == [
                ("Cancelled Custom", None, 1),
                ("Hidden Custom", None, 0),
                ("Public Custom", None, 1),
                ("Real Workshop", 12, 1),
            ]

            remaining_logs = conn.execute(text("SELECT id FROM logs ORDER BY id")).scalars().all()
            assert remaining_logs == [102, 103, 106, 107]

            pen_grant_id = conn.execute(
                text(
                    "SELECT applied_bonus_grant_id FROM pipeline_penalties WHERE id = '00000000-0000-7000-8000-000000000002'"
                )
            ).scalar_one()
            assert pen_grant_id is not None
            penalty = conn.execute(
                text("""
                SELECT g.points, r.department_id, r.member_id
                FROM bonus_grants g JOIN bonus_grant_recipients r ON r.bonus_grant_id = g.id
                WHERE g.id = :id
            """),
                {"id": pen_grant_id},
            ).one()
            assert penalty == (-2, 91, None)

            bonus_sum = conn.execute(
                text(
                    "SELECT SUM(bg.points) FROM bonus_grant_recipients bgr "
                    "JOIN bonus_grants bg ON bg.id = bgr.bonus_grant_id WHERE bgr.member_id = 91"
                )
            ).scalar_one()
            assert bonus_sum == 25

            ts = (
                conn.execute(
                    text(
                        "SELECT bgr.given_at FROM bonus_grant_recipients bgr "
                        "JOIN bonus_grants bg ON bg.id = bgr.bonus_grant_id "
                        "JOIN bonuses b ON b.id = bg.bonus_id "
                        "WHERE b.title = 'Public Custom' ORDER BY bgr.given_at"
                    )
                )
                .scalars()
                .all()
            )
            assert ts == [datetime(2026, 7, 1, 11, 0), datetime(2026, 7, 1, 11, 30)]

        with engine.begin() as conn:
            conn.execute(
                text("""
                INSERT INTO bonuses (id, title, given_at, semester_id)
                VALUES ('00000000-0000-7000-8000-000000000005', 'Post-upgrade award', '2026-07-06', :sem)
            """),
                {"sem": next_semester},
            )
            conn.execute(
                text("""
                INSERT INTO bonus_grants (id, bonus_id, recipient_type, points)
                VALUES ('00000000-0000-7000-8000-000000000006',
                        '00000000-0000-7000-8000-000000000005', 'department', -4)
            """)
            )
            conn.execute(
                text("""
                INSERT INTO bonus_grant_recipients (id, bonus_grant_id, department_id)
                VALUES ('00000000-0000-7000-8000-000000000007', '00000000-0000-7000-8000-000000000006', 91)
            """)
            )
        before["department", next_semester, 91] -= 4

        command.downgrade(config, BEFORE)
        with engine.connect() as conn:
            assert _legacy_totals(conn) == before
            assert conn.execute(
                text("""
                SELECT dl.department_id, l.event_id, m.type, m.value
                FROM pipeline_penalties p
                JOIN departments_logs dl ON dl.log_id = p.applied_log_id
                JOIN logs l ON l.id = p.applied_log_id
                JOIN modifications m ON m.log_id = p.applied_log_id
            """)
            ).one() == (91, 12, "discount", 2)
            restored = conn.execute(
                text("SELECT event_id, dedupe_key, payload FROM push_notifications ORDER BY id")
            ).all()
            assert [(row.event_id, row.dedupe_key) for row in restored] == [
                (10, "points:custom:100:91"),
                (12, "points:custom:104:91"),
            ]
            assert [json.loads(row.payload)["cause"] for row in restored] == [
                {"type": "custom", "log_id": 100},
                {"type": "custom", "log_id": 104},
            ]
        command.upgrade(config, AFTER)
        with engine.connect() as conn:
            assert _current_totals(conn) == before
        command.check(config)
    finally:
        engine.dispose()
        with root_engine.begin() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS `{scratch_db}`"))
        root_engine.dispose()
