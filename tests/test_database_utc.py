"""The connection timezone must not depend on MySQL's server/host timezone."""

from sqlalchemy import create_engine, text

from app.DB.main import _build_connect_args
from app.DB.schema import ClubMemberships, SemesterDepartments
from app.DB.semesters import get_semester_by_hijri_code
from app.DB.club_structure import get_role_by_key


def test_new_and_reconnected_mysql_sessions_explicitly_use_utc(database_url):
    configured_engine = create_engine(database_url, connect_args=_build_connect_args(database_url))
    try:
        for _ in range(2):
            with configured_engine.connect() as connection:
                assert connection.scalar(text("SELECT @@session.time_zone")) == "+00:00"
            configured_engine.dispose()
    finally:
        configured_engine.dispose()


def test_membership_insert_default_is_utc_with_microsecond_precision(db_session, seed_refs):
    semester_id = get_semester_by_hijri_code(db_session, 475).id
    db_session.add(SemesterDepartments(semester_id=semester_id, department_id=seed_refs.dept_design.id))
    db_session.flush()
    membership = ClubMemberships(
        semester_id=semester_id,
        department_id=seed_refs.dept_design.id,
        member_id=seed_refs.ahmed.id,
        role_id=get_role_by_key(db_session, "member").id,
        created_by="clerk_admin",
    )
    db_session.add(membership)
    db_session.flush()
    age = db_session.scalar(
        text("SELECT TIMESTAMPDIFF(MICROSECOND, created_at, UTC_TIMESTAMP(6)) FROM club_memberships WHERE id = :id"),
        {"id": membership.id},
    )
    assert 0 <= age < 5_000_000
    precision = db_session.scalar(
        text(
            "SELECT DATETIME_PRECISION FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = 'club_memberships' AND column_name = 'created_at'"
        )
    )
    assert precision == 6


def test_non_mysql_engines_do_not_receive_mysql_connection_options():
    assert _build_connect_args("sqlite://") == {}
