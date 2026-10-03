"""7d5e6f7a8b9c turns integer ids into UUIDs: every row and every link between rows must survive it.

It runs against the real schema: down to the revision before, rows written the
way the trial wrote them, then up to head again.
"""

import uuid

from alembic import command
from alembic.config import Config
from sqlalchemy import bindparam, delete, select, text
from sqlalchemy.orm import Session

from app.DB.schema import Departments, DepartmentsType, Members, Semesters
from tests.factories import make_member

BEFORE = "6c4d5e6f7a8b"


def _is_uuid(value) -> bool:
    try:
        uuid.UUID(str(value))
    except ValueError:
        return False
    return True


def test_integer_ids_become_uuids_and_every_link_survives(engine):
    suffix = uuid.uuid4().hex[:8]
    with Session(engine) as session:
        department = Departments(name=f"Migrated {suffix}", ar_name="قسم", type=DepartmentsType.PRACTICAL)
        member = Members(**make_member(name="Migrated", uni_id=f"mig{suffix}", email=f"{suffix}@example.com"))
        session.add_all([department, member])
        session.commit()
        department_id, member_id = department.id, member.id
        semester_id = session.scalar(select(Semesters.id).where(Semesters.hijri_code == 475))

    config = Config("alembic.ini")
    command.downgrade(config, BEFORE)
    try:
        with engine.begin() as conn:
            run = lambda sql, **p: conn.execute(text(sql), {"d": department_id, "m": member_id, **p})  # noqa: E731
            run("INSERT INTO event_requests (department_id, created_by, title) VALUES (:d, :m, 'First')")
            first = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()
            run("INSERT INTO event_requests (department_id, created_by, title) VALUES (:d, :m, 'Second')")
            second = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()
            for request_id in (first, second):
                run("INSERT INTO event_request_partners (request_id, department_id) VALUES (:r, :d)", r=request_id)
                run(
                    "INSERT INTO event_request_tasks (request_id, team, status) VALUES (:r, 'design', 'open')",
                    r=request_id,
                )
                run(
                    "INSERT INTO pipeline_notifications (department_id, request_id, kind) "
                    "VALUES (:d, :r, 'request_received')",
                    r=request_id,
                )
                notification = conn.execute(text("SELECT LAST_INSERT_ID()")).scalar()
                run(
                    "INSERT INTO pipeline_notification_reads (notification_id, member_id) VALUES (:n, :m)",
                    n=notification,
                )
            run(
                "INSERT INTO pipeline_penalties (request_id, department_id, late_days, points, reason) "
                "VALUES (:r, :d, 2, 2, 'late')",
                r=second,
            )
            run(
                "INSERT INTO permission_grants (semester_id, department_id, member_id, permission, granted_by) "
                "VALUES (:s, :d, :m, 'events.edit', :m)",
                s=semester_id,
            )
    finally:
        command.upgrade(config, "head")

    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, title FROM event_requests WHERE department_id = :d ORDER BY title"), {"d": department_id}
        ).all()
        assert [title for _id, title in rows] == ["First", "Second"]
        assert all(_is_uuid(request_id) for request_id, _ in rows)
        by_title = {title: request_id for request_id, title in rows}

        for table in ("event_request_partners", "event_request_tasks", "pipeline_notifications"):
            linked = conn.execute(
                text(f"SELECT request_id FROM {table} WHERE request_id IN :ids").bindparams(
                    bindparam("ids", expanding=True)
                ),
                {"ids": list(by_title.values())},
            ).scalars()
            assert sorted(linked) == sorted(by_title.values()), table
        penalty = conn.execute(
            text("SELECT id, request_id FROM pipeline_penalties WHERE department_id = :d"), {"d": department_id}
        ).one()
        assert _is_uuid(penalty.id) and penalty.request_id == by_title["Second"]

        reads = conn.execute(
            text(
                "SELECT n.request_id FROM pipeline_notification_reads r "
                "JOIN pipeline_notifications n ON n.id = r.notification_id WHERE r.member_id = :m"
            ),
            {"m": member_id},
        ).scalars()
        assert sorted(reads) == sorted(by_title.values())

        grant = conn.execute(text("SELECT id FROM permission_grants WHERE member_id = :m"), {"m": member_id}).scalar()
        assert _is_uuid(grant)

    with Session(engine) as session:
        # The foreign keys cascade, so the requests' rows go with them.
        session.execute(text("DELETE FROM permission_grants WHERE member_id = :m"), {"m": member_id})
        session.execute(text("DELETE FROM pipeline_notification_reads WHERE member_id = :m"), {"m": member_id})
        session.execute(text("DELETE FROM event_requests WHERE department_id = :d"), {"d": department_id})
        session.execute(delete(Departments).where(Departments.id == department_id))
        session.execute(delete(Members).where(Members.id == member_id))
        session.commit()
