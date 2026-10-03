"""8e6f7a8b9c0d deletes the requests left published after their event was deleted."""

import uuid

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.DB.schema import Departments, DepartmentsType, Members
from tests.factories import make_member

BEFORE = "7d5e6f7a8b9c"


def test_requests_published_without_an_event_are_deleted(engine):
    suffix = uuid.uuid4().hex[:8]
    with Session(engine) as session:
        department = Departments(name=f"Orphans {suffix}", ar_name="قسم", type=DepartmentsType.PRACTICAL)
        member = Members(**make_member(name="Orphans", uni_id=f"orp{suffix}", email=f"{suffix}@example.com"))
        session.add_all([department, member])
        session.commit()
        department_id, member_id = department.id, member.id

    orphan, draft = str(uuid.uuid4()), str(uuid.uuid4())
    config = Config("alembic.ini")
    command.downgrade(config, BEFORE)
    try:
        with engine.begin() as conn:
            for request_id, stage in ((orphan, "published"), (draft, "draft")):
                conn.execute(
                    text(
                        "INSERT INTO event_requests (id, department_id, created_by, title, stage, "
                        "start_date, end_date) VALUES (:id, :d, :m, 'Orphan', :stage, '2026-07-20', '2026-07-20')"
                    ),
                    {"id": request_id, "d": department_id, "m": member_id, "stage": stage},
                )
                conn.execute(
                    text(
                        "INSERT INTO event_request_tasks (id, request_id, team, status) VALUES (:t, :r, 'design', 'done')"
                    ),
                    {"t": str(uuid.uuid4()), "r": request_id},
                )
    finally:
        command.upgrade(config, "head")

    with engine.begin() as conn:
        left = conn.execute(text("SELECT id FROM event_requests WHERE department_id = :d"), {"d": department_id})
        assert list(left.scalars()) == [draft]
        tasks = conn.execute(text("SELECT request_id FROM event_request_tasks WHERE request_id = :r"), {"r": orphan})
        assert list(tasks.scalars()) == []
        conn.execute(text("DELETE FROM event_requests WHERE department_id = :d"), {"d": department_id})
        conn.execute(text("DELETE FROM departments WHERE id = :d"), {"d": department_id})
        conn.execute(text("DELETE FROM members WHERE id = :m"), {"m": member_id})
