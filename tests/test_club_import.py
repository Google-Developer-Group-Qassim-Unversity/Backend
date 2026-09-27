"""Importing past club structures from CSV: resolution, all-or-nothing, and the roster rules."""

from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.DB.schema import ClubMemberships, Departments, DepartmentsType, SemesterDepartments
from app.DB.semesters import get_semester_by_hijri_code
from app.services.club_import import ImportFailed, import_structure, read_csv
from scripts import import_club_structure as script

HEADER = "semester,department,member,role,department_name,department_ar_name\n"


def write(tmp_path: Path, body: str, name: str = "471.csv") -> Path:
    path = tmp_path / name
    path.write_text(HEADER + body, encoding="utf-8")
    return path


def run(db_session, tmp_path, body):
    return import_structure(db_session, read_csv(write(tmp_path, body)), actor="import:test")


def roster(db_session, code: int, department_id: int) -> dict[int, list[str]]:
    semester = get_semester_by_hijri_code(db_session, code)
    assert semester is not None
    rows = db_session.scalars(
        select(ClubMemberships).where(
            ClubMemberships.semester_id == semester.id, ClubMemberships.department_id == department_id
        )
    ).all()
    result: dict[int, list[str]] = {}
    for row in rows:
        result.setdefault(row.member_id, []).append(row.role.key)
    return {member: sorted(roles) for member, roles in result.items()}


def count(db_session) -> int:
    return db_session.scalar(select(func.count()).select_from(ClubMemberships)) or 0


def test_imports_a_semester_with_every_way_of_naming_things(db_session, tmp_path, seed_refs):
    design, ahmed, sara = seed_refs.dept_design, seed_refs.ahmed, seed_refs.sara
    report = run(
        db_session,
        tmp_path,
        f"471,Design,{ahmed.uni_id},قائد,,\n"
        f"471,التصميم,{sara.email.upper()},member,,\n"
        f"471,{seed_refs.dept_business.id},#{sara.id},deputy,Old Business,الأعمال القديمة\n",
    )
    # The leader's member row comes automatically.
    assert roster(db_session, 471, design.id) == {ahmed.id: ["leader", "member"], sara.id: ["member"]}
    assert roster(db_session, 471, seed_refs.dept_business.id) == {sara.id: ["member", "vp"]}
    semester = get_semester_by_hijri_code(db_session, 471)
    assert semester is not None
    named = db_session.get(SemesterDepartments, (semester.id, seed_refs.dept_business.id))
    assert (named.name, named.ar_name) == ("Old Business", "الأعمال القديمة")
    assert (report.departments_added, report.names_recorded, report.roles_added) == (2, 1, 5)
    assert report.semesters == {471}


def test_running_it_again_changes_nothing(db_session, tmp_path, seed_refs):
    body = f"471,Design,{seed_refs.ahmed.uni_id},leader,,\n"
    run(db_session, tmp_path, body)
    before = count(db_session)
    report = run(db_session, tmp_path, body)
    assert count(db_session) == before
    assert (report.roles_added, report.roles_already_there, report.departments_added) == (0, 1, 0)


def test_archived_departments_can_be_imported_into_past_semesters(db_session, tmp_path, seed_refs):
    gone = Departments(name="Innovation", ar_name="قسم الابتكار", type=DepartmentsType.PRACTICAL, active=0)
    db_session.add(gone)
    db_session.flush()
    run(db_session, tmp_path, f"471,قسم الابتكار,{seed_refs.ahmed.uni_id},member,,\n")
    assert roster(db_session, 471, gone.id) == {seed_refs.ahmed.id: ["member"]}


@pytest.mark.parametrize(
    ("row", "problem"),
    [
        ("461,Design,111111111,member,,", "semester '461' does not exist"),
        ("abc,Design,111111111,member,,", "semester 'abc' does not exist"),
        ("471,Nope,111111111,member,,", "department 'Nope' matches no department"),
        ("471,Design,000000000,member,,", "member '000000000' has no members row"),
        ("471,Design,nobody@example.com,member,,", "has no members row"),
        ("471,Design,111111111,president,,", "role 'president' is not leader, vp or member"),
    ],
)
def test_one_bad_row_means_nothing_is_imported(db_session, tmp_path, seed_refs, row, problem):
    good = f"471,Design,{seed_refs.sara.uni_id},member,,\n"
    with pytest.raises(ImportFailed) as failure:
        run(db_session, tmp_path, good + row + "\n")
    assert any(problem in error and "471.csv:3" in error for error in failure.value.errors), failure.value.errors
    assert count(db_session) == 0


def test_every_problem_is_reported_at_once(db_session, tmp_path):
    with pytest.raises(ImportFailed) as failure:
        run(db_session, tmp_path, "461,Design,111111111,member,,\n471,Nope,111111111,member,,\n")
    assert len(failure.value.errors) == 2


def test_a_full_seat_fails_the_whole_import(db_session, tmp_path, seed_refs):
    body = f"471,Design,{seed_refs.ahmed.uni_id},leader,,\n471,Design,{seed_refs.sara.uni_id},leader,,\n"
    with pytest.raises(ImportFailed) as failure:
        run(db_session, tmp_path, body)
    assert "semester 471, Design" in failure.value.errors[0]
    assert "taken" in failure.value.errors[0]


def test_conflicting_names_for_one_semester_are_refused(db_session, tmp_path, seed_refs):
    body = (
        f"471,Design,{seed_refs.ahmed.uni_id},member,Design A,\n471,Design,{seed_refs.sara.uni_id},member,Design B,\n"
    )
    with pytest.raises(ImportFailed, match="names differ"):
        run(db_session, tmp_path, body)


def test_a_recorded_name_is_never_overwritten(db_session, tmp_path, seed_refs):
    run(db_session, tmp_path, f"471,Design,{seed_refs.ahmed.uni_id},member,Design A,\n")
    with pytest.raises(ImportFailed, match="already records the name 'Design A'"):
        run(db_session, tmp_path, f"471,Design,{seed_refs.sara.uni_id},member,Design B,\n")


def test_header_is_checked(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("semester,department,person\n471,Design,111111111\n", encoding="utf-8")
    with pytest.raises(ImportFailed) as failure:
        read_csv(path)
    assert failure.value.errors == [
        "header: missing column 'member'",
        "header: missing column 'role'",
        "header: unknown column 'person'",
    ]


def test_script_dry_run_commits_nothing_and_apply_commits(db_session, tmp_path, seed_refs, capsys):
    path = write(tmp_path, f"471,Design,{seed_refs.ahmed.uni_id},vp,,\n")
    assert script.main([path], apply=False) == 0
    assert "Dry run" in capsys.readouterr().out
    assert count(db_session) == 0

    assert script.main([path], apply=True) == 0
    assert "Applied" in capsys.readouterr().out
    assert roster(db_session, 471, seed_refs.dept_design.id) == {seed_refs.ahmed.id: ["member", "vp"]}


def test_script_reports_problems_and_exits_non_zero(tmp_path, capsys):
    path = write(tmp_path, "461,Design,111111111,member,,\n")
    assert script.main([path], apply=True) == 1
    assert "Nothing imported" in capsys.readouterr().err
