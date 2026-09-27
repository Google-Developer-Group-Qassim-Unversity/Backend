"""Import a past semester's club structure from a CSV file.

One row per role someone held:

    semester,department,member,role[,department_name,department_ar_name]
    461,Development,441234567,leader
    461,Development,sara@qu.edu.sa,member
    461,7,#1214,vp,Innovation,قسم الابتكار

- ``semester``: the Hijri code (461). The semester must already exist - add it
  in Settings → Semesters first.
- ``department``: a department id, or its current English or Arabic name.
  The department must exist; one that is gone today can be created and
  archived in the admin app first.
- ``member``: a university id, an email, or ``#<members.id>``. Nobody is
  created: a person without a ``members`` row fails the import.
- ``role``: ``leader``, ``vp`` or ``member`` (``deputy``, قائد, نائب, عضو also work).
  Leaders and VPs get their member row automatically.
- ``department_name`` / ``department_ar_name`` (optional): the name the
  department had that semester, when it differs from today's.

Everything is resolved and checked before anything is written, and the whole
import runs in one transaction: one bad row means nothing is imported. Rows
already in the database are skipped, so an import can be run again safely.
The roster rules (member rows, seat limits) are the service's, not re-implemented.
"""

import csv
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.DB import club_structure as queries
from app.DB import semesters as semesters_queries
from app.DB.schema import Departments, Members, SemesterDepartments
from app.exceptions import ClubStructureConflict
from app.services import club_structure as service

REQUIRED_COLUMNS = ("semester", "department", "member", "role")
OPTIONAL_COLUMNS = ("department_name", "department_ar_name")
ROLE_ALIASES = {
    "leader": "leader",
    "قائد": "leader",
    "vp": "vp",
    "deputy": "vp",
    "نائب": "vp",
    "member": "member",
    "عضو": "member",
}


class ImportFailed(Exception):
    """The file cannot be imported. Nothing was written."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(f"{len(errors)} problem(s):\n  - " + "\n  - ".join(errors))


@dataclass
class ImportRow:
    source: str
    line: int
    semester: str
    department: str
    member: str
    role: str
    department_name: str | None = None
    department_ar_name: str | None = None


@dataclass
class ImportReport:
    semesters: set[int] = field(default_factory=set)
    departments_added: int = 0
    names_recorded: int = 0
    roles_added: int = 0
    roles_already_there: int = 0

    def lines(self) -> list[str]:
        return [
            f"semesters: {', '.join(str(code) for code in sorted(self.semesters))}",
            f"departments added to a semester: {self.departments_added}",
            f"semester names recorded: {self.names_recorded}",
            f"roles added (including automatic member rows): {self.roles_added}",
            f"roles already there, skipped: {self.roles_already_there}",
        ]


def read_csv(path: Path) -> list[ImportRow]:
    with path.open(newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        columns = [column.strip() for column in reader.fieldnames or []]
        missing = [column for column in REQUIRED_COLUMNS if column not in columns]
        unknown = [column for column in columns if column not in REQUIRED_COLUMNS + OPTIONAL_COLUMNS]
        problems = [f"header: missing column {column!r}" for column in missing]
        problems += [f"header: unknown column {column!r}" for column in unknown]
        if problems:
            raise ImportFailed(problems)
        rows = []
        for line, raw in enumerate(reader, start=2):
            values = {key.strip(): (value or "").strip() for key, value in raw.items() if key}
            if not any(values.values()):
                continue
            rows.append(
                ImportRow(
                    source=path.name,
                    line=line,
                    semester=values["semester"],
                    department=values["department"],
                    member=values["member"],
                    role=values["role"],
                    department_name=values.get("department_name") or None,
                    department_ar_name=values.get("department_ar_name") or None,
                )
            )
    return rows


def _find_member(session: Session, reference: str) -> list[Members]:
    if reference.startswith("#") and reference[1:].isdigit():
        return list(session.scalars(select(Members).where(Members.id == int(reference[1:]))))
    if "@" in reference:
        return list(session.scalars(select(Members).where(func.lower(Members.email) == reference.lower())))
    return list(session.scalars(select(Members).where(Members.uni_id == reference)))


def _find_department(session: Session, reference: str) -> list[Departments]:
    if reference.isdigit():
        return list(session.scalars(select(Departments).where(Departments.id == int(reference))))
    folded = re.sub(r"\s+", " ", reference).lower()
    return [
        department
        for department in session.scalars(select(Departments))
        if folded in (re.sub(r"\s+", " ", department.name).lower(), re.sub(r"\s+", " ", department.ar_name).lower())
    ]


def import_structure(session: Session, rows: list[ImportRow], *, actor: str) -> ImportReport:
    """Resolve every row, then write them all. Raises ``ImportFailed`` and writes nothing on any problem.

    The caller commits (or rolls back, for a dry run).
    """
    errors: list[str] = []
    semesters: dict[str, str] = {}
    members: dict[str, int] = {}
    departments: dict[str, int] = {}
    # (semester id, department id) -> {member id: {role keys}}
    plan: dict[tuple[str, int], dict[int, set[str]]] = defaultdict(lambda: defaultdict(set))
    names: dict[tuple[str, int], tuple[str | None, str | None, str]] = {}
    codes: dict[str, int] = {}

    if not rows:
        raise ImportFailed(["the file has no rows"])

    for row in rows:
        where = f"{row.source}:{row.line}"
        if row.semester not in semesters:
            semester = (
                semesters_queries.get_semester_by_hijri_code(session, int(row.semester))
                if row.semester.isdigit()
                else None
            )
            if semester is None:
                errors.append(f"{where}: semester {row.semester!r} does not exist - add it in Settings → Semesters")
                continue
            semesters[row.semester] = semester.id
            codes[semester.id] = semester.hijri_code
        if row.department not in departments:
            found = _find_department(session, row.department)
            if len(found) != 1:
                what = "matches no department" if not found else f"matches {len(found)} departments"
                errors.append(f"{where}: department {row.department!r} {what}")
                continue
            departments[row.department] = found[0].id
        if row.member not in members:
            found_members = _find_member(session, row.member)
            if len(found_members) != 1:
                what = "has no members row" if not found_members else f"matches {len(found_members)} members"
                errors.append(f"{where}: member {row.member!r} {what}")
                continue
            members[row.member] = found_members[0].id
        role = ROLE_ALIASES.get(row.role.lower())
        if role is None:
            errors.append(f"{where}: role {row.role!r} is not leader, vp or member")
            continue

        scope = (semesters[row.semester], departments[row.department])
        plan[scope][members[row.member]].add(role)
        if row.department_name or row.department_ar_name:
            given = (row.department_name, row.department_ar_name, where)
            earlier = names.setdefault(scope, given)
            if earlier[:2] != given[:2]:
                errors.append(f"{where}: department names differ from {earlier[2]} for the same semester")

    for (semester_id, department_id), (name, ar_name, where) in names.items():
        existing = queries.get_semester_department(session, semester_id, department_id)
        for new, old in (
            (name, existing.name if existing else None),
            (ar_name, existing.ar_name if existing else None),
        ):
            if new and old and new != old:
                errors.append(f"{where}: that semester already records the name {old!r}, not {new!r}")

    if errors:
        raise ImportFailed(errors)

    report = ImportReport(semesters=set(codes.values()))
    for (semester_id, department_id), people in plan.items():
        try:
            _apply_scope(session, semester_id, department_id, people, names, report, actor)
        except ClubStructureConflict as exc:
            # Seat limits and the like; the caller's rollback undoes the rows written so far.
            department = session.get(Departments, department_id)
            raise ImportFailed(
                [f"semester {codes[semester_id]}, {department.name if department else department_id}: {exc.detail}"]
            ) from exc
    session.flush()
    return report


def _apply_scope(
    session: Session,
    semester_id: str,
    department_id: int,
    people: dict[int, set[str]],
    names: dict[tuple[str, int], tuple[str | None, str | None, str]],
    report: ImportReport,
    actor: str,
) -> None:
    """Write one department's roster for one semester, skipping what is already there."""
    scope_row = queries.get_semester_department(session, semester_id, department_id)
    if scope_row is None:
        # Straight into the table: past semesters include departments archived today,
        # which the admin "add to semester" action rightly refuses.
        scope_row = SemesterDepartments(semester_id=semester_id, department_id=department_id)
        session.add(scope_row)
        session.flush()
        report.departments_added += 1
    name, ar_name, _ = names.get((semester_id, department_id), (None, None, ""))
    if (name and not scope_row.name) or (ar_name and not scope_row.ar_name):
        scope_row.name = scope_row.name or name
        scope_row.ar_name = scope_row.ar_name or ar_name
        report.names_recorded += 1

    for member_id, roles in people.items():
        held = {row.role.key for row in queries.get_member_roles(session, semester_id, department_id, member_id)}
        if service.MEMBER_ROLE not in held:
            service.add_member(session, semester_id, department_id, member_id, actor=actor)
            report.roles_added += 1
        elif service.MEMBER_ROLE in roles:
            report.roles_already_there += 1
        for role in sorted(roles - {service.MEMBER_ROLE}):
            if role in held:
                report.roles_already_there += 1
                continue
            service.grant_role(session, semester_id, department_id, member_id, role, actor=actor)
            report.roles_added += 1
