"""The connection timezone must not depend on MySQL's server/host timezone."""

from sqlalchemy import create_engine, text

from app.DB.main import _build_connect_args
from app.DB.schema import ClubAssignments


def test_new_and_reconnected_mysql_sessions_explicitly_use_utc(database_url):
    configured_engine = create_engine(database_url, connect_args=_build_connect_args(database_url))
    try:
        for _ in range(2):
            with configured_engine.connect() as connection:
                assert connection.scalar(text("SELECT @@session.time_zone")) == "+00:00"
            configured_engine.dispose()
    finally:
        configured_engine.dispose()


def test_assignment_insert_default_is_utc_with_microsecond_precision(db_session, seed_refs):
    assignment = ClubAssignments(
        member_id=seed_refs.ahmed.id, department_id=seed_refs.dept_design.id, role="member", changed_by="clerk_admin"
    )
    db_session.add(assignment)
    db_session.flush()
    age = db_session.scalar(
        text("SELECT TIMESTAMPDIFF(MICROSECOND, starts_at, UTC_TIMESTAMP(6)) FROM club_assignments WHERE id = :id"),
        {"id": assignment.id},
    )
    assert 0 <= age < 5_000_000
    precision = db_session.scalar(
        text(
            "SELECT DATETIME_PRECISION FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = 'club_assignments' AND column_name = 'starts_at'"
        )
    )
    assert precision == 6


def test_non_mysql_engines_do_not_receive_mysql_connection_options():
    assert _build_connect_args("sqlite://") == {}
