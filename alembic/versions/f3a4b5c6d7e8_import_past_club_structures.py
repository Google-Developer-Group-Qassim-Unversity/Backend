"""import the 471, 472 and 475 club structures

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-09-27 18:00:00.000000

Data only. Loads the rosters of Fall 2025 (471), Spring 2026 (472) and Summer
2026 (475), transcribed from the club's structure posters and matched against
``members``. Only people with a confirmed ``members`` row are here; the rest are
added by a later revision once they are resolved. The file holds ids only -
the names live in ``members``.

- Every department keeps today's name in every year; no per-semester names.
- In 471, "ريادة الأعمال والتقنية" had two halves: the business half is
  Entrepreneurship (9) and the technology half is Development (1).
- 472's "قسم الابتكار" becomes a new archived department, Innovation / الابتكار.
- Departments 2 and 5 have their English names swapped: 2 is التنظيم والاجتماعات
  (Organization and Meetings) and 5 is إدارة البرامج والفعاليات (Program and Event
  Management). The Arabic names were right, so the English ones are swapped back.
- A leader or VP also gets a member row, like the service does.

Only runs against the production data it was built from: on a database where
none of these members exist (a fresh or test database) it does nothing, and on
one where only some exist it aborts before writing. Rows already present are
skipped, so it is safe on a database the import script already ran against.
"""

from collections import defaultdict
import logging
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f3a4b5c6d7e8"
down_revision: Union[str, Sequence[str], None] = "e2f3a4b5c6d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

logger = logging.getLogger("alembic.runtime.migration")

ACTOR = "migration:f3a4b5c6d7e8"
INNOVATION = {"name": "Innovation", "ar_name": "الابتكار", "type": "practical", "icon": "lightbulb"}

# (semester hijri code, department, members.id, role). The department is an id,
# "leadership" (the club leadership department) or "innovation" (created here).
ROWS = [
    # 471
    (471, "leadership", 1214, "leader"),
    (471, 4, 409, "leader"),
    (471, 4, 921, "vp"),
    (471, 4, 492, "member"),
    (471, 5, 455, "vp"),
    (471, 5, 891, "member"),
    (471, 5, 390, "member"),
    (471, 2, 1395, "leader"),
    (471, 2, 212, "vp"),
    (471, 2, 496, "member"),
    (471, 2, 1176, "member"),
    (471, 3, 461, "leader"),
    (471, 3, 350, "vp"),
    (471, 3, 907, "member"),
    (471, 3, 342, "member"),
    (471, 3, 352, "member"),
    (471, 3, 298, "member"),
    (471, 7, 511, "leader"),
    (471, 7, 1174, "vp"),
    (471, 7, 299, "member"),
    (471, 7, 1179, "member"),
    (471, 7, 552, "member"),
    (471, 6, 1360, "leader"),
    (471, 6, 1894, "vp"),
    (471, 6, 489, "member"),
    (471, 8, 456, "leader"),
    (471, 8, 457, "vp"),
    (471, 8, 1247, "member"),
    (471, 8, 1648, "member"),
    (471, 8, 1404, "member"),
    (471, 9, 404, "leader"),
    (471, 9, 403, "vp"),
    (471, 9, 431, "member"),
    (471, 9, 430, "member"),
    (471, 9, 597, "member"),
    (471, 1, 1215, "leader"),
    (471, 1, 505, "vp"),
    # 472
    (472, "leadership", 1214, "leader"),
    (472, 15, 403, "member"),
    (472, 4, 1538, "leader"),
    (472, 4, 409, "vp"),
    (472, "innovation", 450, "member"),
    (472, "innovation", 448, "member"),
    (472, 1, 1215, "leader"),
    (472, 1, 505, "vp"),
    (472, 1, 1031, "member"),
    (472, 1, 188, "member"),
    (472, 2, 212, "leader"),
    (472, 2, 496, "vp"),
    (472, 2, 1395, "member"),
    (472, 2, 1176, "member"),
    (472, 5, 201, "leader"),
    (472, 5, 411, "vp"),
    (472, 5, 390, "member"),
    (472, 5, 1400, "member"),
    (472, 5, 3414, "member"),
    (472, 3, 461, "leader"),
    (472, 3, 352, "vp"),
    (472, 3, 342, "member"),
    (472, 3, 891, "member"),
    (472, 3, 487, "member"),
    (472, 3, 251, "member"),
    (472, 10, 1295, "leader"),
    (472, 10, 1293, "member"),
    (472, 10, 1390, "member"),
    (472, 10, 1294, "member"),
    (472, 6, 489, "leader"),
    (472, 6, 266, "vp"),
    (472, 6, 1724, "member"),
    (472, 6, 481, "member"),
    (472, 6, 1894, "member"),
    (472, 6, 391, "member"),
    (472, 8, 1404, "leader"),
    (472, 8, 1247, "vp"),
    (472, 8, 298, "member"),
    (472, 8, 369, "member"),
    (472, 7, 299, "vp"),
    (472, 7, 455, "member"),
    (472, 7, 1179, "member"),
    (472, 7, 552, "member"),
    (472, 7, 284, "member"),
    (472, 9, 1532, "vp"),
    (472, 9, 1531, "member"),
    (472, 9, 1481, "member"),
    (472, 9, 1143, "member"),
    (472, 9, 1533, "member"),
    # 475
    (475, 12, 411, "member"),
    (475, 12, 492, "member"),
    (475, 12, 1293, "member"),
    (475, 12, 391, "member"),
    (475, 12, 487, "member"),
    (475, 13, 481, "member"),
    (475, 13, 3414, "member"),
    (475, 13, 1401, "member"),
    (475, 13, 978, "member"),
    (475, 13, 496, "member"),
    (475, 13, 598, "member"),
    (475, 11, 266, "member"),
    (475, 11, 1294, "member"),
]


def _members_present(conn) -> set[int]:
    ids = sorted({member for _, _, member, _ in ROWS})
    found = conn.execute(
        sa.text("SELECT id FROM members WHERE id IN :ids").bindparams(sa.bindparam("ids", expanding=True)), {"ids": ids}
    )
    return {row.id for row in found}


def upgrade() -> None:
    conn = op.get_bind()
    wanted = {member for _, _, member, _ in ROWS}
    present = _members_present(conn)
    if not present:
        logger.info(f"{revision}: none of the {len(wanted)} members exist - not this club's data, nothing to import")
        return

    # Every check before any write.
    problems = [f"member {member} does not exist" for member in sorted(wanted - present)]
    semesters = dict(
        conn.execute(sa.text("SELECT hijri_code, id FROM semesters WHERE hijri_code IN (471, 472, 475)")).tuples().all()
    )
    problems += [f"semester {code} does not exist" for code in (471, 472, 475) if code not in semesters]
    leadership = conn.execute(sa.text("SELECT id FROM departments WHERE is_club_leadership = 1")).scalar()
    if leadership is None:
        problems.append("there is no club leadership department")
    roles = dict(conn.execute(sa.text("SELECT `key`, id FROM club_roles")).tuples().all())
    problems += [f"club role {key} does not exist" for key in ("leader", "vp", "member") if key not in roles]
    numbered = sorted({dept for _, dept, _, _ in ROWS if isinstance(dept, int)})
    existing = {row.id for row in conn.execute(sa.text("SELECT id FROM departments"))}
    problems += [f"department {dept} does not exist" for dept in numbered if dept not in existing]
    if problems:
        raise RuntimeError("Club structure import aborted before any change was made:\n  - " + "\n  - ".join(problems))

    # Departments 2 and 5: swap the English names back, only if they are still the swapped pair.
    names = dict(conn.execute(sa.text("SELECT id, name FROM departments WHERE id IN (2, 5)")).tuples().all())
    if names == {2: "Program and Event Management", 5: "Organization and Meetings"}:
        conn.execute(sa.text("UPDATE departments SET name = 'Organization and Meetings' WHERE id = 2"))
        conn.execute(sa.text("UPDATE departments SET name = 'Program and Event Management' WHERE id = 5"))

    innovation = conn.execute(
        sa.text("SELECT id FROM departments WHERE name = :name"), {"name": INNOVATION["name"]}
    ).scalar()
    if innovation is None:
        conn.execute(
            sa.text(
                "INSERT INTO departments (name, ar_name, type, icon, active) VALUES (:name, :ar_name, :type, :icon, 0)"
            ),
            INNOVATION,
        )
        innovation = conn.execute(
            sa.text("SELECT id FROM departments WHERE name = :name"), {"name": INNOVATION["name"]}
        ).scalar()

    def department(dept):
        return {"leadership": leadership, "innovation": innovation}.get(dept, dept)

    # Seat limits, counted with what is already there.
    limits = {("leader", leadership): 2}
    seats: dict[tuple, set[int]] = defaultdict(set)
    for code, dept, member, role in ROWS:
        if role != "member":
            seats[(semesters[code], department(dept), role)].add(member)
    for (semester_id, department_id, role), holders in seats.items():
        held = {
            row.member_id
            for row in conn.execute(
                sa.text(
                    "SELECT member_id FROM club_memberships WHERE semester_id = :s AND department_id = :d AND role_id = :r"
                ),
                {"s": semester_id, "d": department_id, "r": roles[role]},
            )
        }
        if len(held | holders) > limits.get((role, department_id), 1):
            raise RuntimeError(f"Club structure import aborted: too many {role}s for department {department_id}")

    for code, dept, member, role in ROWS:
        params = {"s": semesters[code], "d": department(dept), "m": member, "by": ACTOR}
        conn.execute(
            sa.text("INSERT IGNORE INTO semester_departments (semester_id, department_id) VALUES (:s, :d)"), params
        )
        for key in dict.fromkeys(["member", role]):
            exists = conn.execute(
                sa.text(
                    "SELECT 1 FROM club_memberships "
                    "WHERE semester_id = :s AND department_id = :d AND member_id = :m AND role_id = :r"
                ),
                {**params, "r": roles[key]},
            ).first()
            if exists:
                continue
            conn.execute(
                sa.text(
                    "INSERT INTO club_memberships (id, semester_id, department_id, member_id, role_id, created_by) "
                    "VALUES (UUID(), :s, :d, :m, :r, :by)"
                ),
                {**params, "r": roles[key]},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO club_membership_changes "
                    "(id, semester_id, department_id, member_id, role_id, action, actor) "
                    "VALUES (UUID(), :s, :d, :m, :r, 'added', :by)"
                ),
                {**params, "r": roles[key]},
            )


def downgrade() -> None:
    """Removes only what this revision added. The department names stay corrected."""
    conn = op.get_bind()
    conn.execute(sa.text("DELETE FROM club_membership_changes WHERE actor = :a"), {"a": ACTOR})
    conn.execute(sa.text("DELETE FROM club_memberships WHERE created_by = :a"), {"a": ACTOR})
    conn.execute(
        sa.text(
            "DELETE sd FROM semester_departments sd JOIN departments d ON d.id = sd.department_id "
            "WHERE d.name = :name AND NOT EXISTS (SELECT 1 FROM club_memberships m WHERE m.department_id = d.id)"
        ),
        {"name": INNOVATION["name"]},
    )
    conn.execute(
        sa.text(
            "DELETE FROM departments WHERE name = :name AND active = 0 "
            "AND id NOT IN (SELECT department_id FROM departments_logs) "
            "AND id NOT IN (SELECT department_id FROM semester_departments)"
        ),
        {"name": INNOVATION["name"]},
    )
