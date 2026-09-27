"""club structure per semester; drop club_assignments

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-09-27 12:00:00.000000

Replaces the ``club_assignments`` tenure log with a roster per semester:

``club_roles``               member / vp / leader, each with a default seat limit
``department_role_limits``   a per-department override of that limit (Leadership: 2 leaders)
``semester_departments``     which departments existed in a semester, with the name they had then
``club_memberships``         (semester, department, member, role) - one row per role held
``club_membership_changes``  append-only log of who added or removed which role

A leader or VP also has an explicit ``member`` row in the same department.

The two presidents stop being a special case with no department: they become
``member`` + ``leader`` of a new "Leadership / القادة" department, marked by
``departments.is_club_leadership``. The Board is an ordinary department whose
only difference, being left out of the department ranking, is the new
``departments.show_in_leaderboard`` setting. ``leadership_enabled`` is dropped.

Data: every semester gets the departments that earned department points in it;
the current semester (by the calendar rule in ``app/semesters.py``) also gets
every active department. The open ``club_assignments`` rows become the current
semester's roster (deputy -> vp, presidents -> Leadership). Closed rows are
discarded and the table is dropped; the regular backups hold the old data.

MySQL cannot roll back DDL, so every check runs before the first CREATE.
"""

from datetime import datetime
from typing import Sequence, Union
from zoneinfo import ZoneInfo

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "e2f3a4b5c6d7"
down_revision: Union[str, Sequence[str], None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


UUID_TYPE = mysql.CHAR(36, charset="ascii", collation="ascii_bin")
DEPARTMENT_ID = mysql.INTEGER(unsigned=True)

# The roles the club uses. Their keys are what the API and the service refer to.
ROLES = [
    # key, name, ar_name, max_holders, sort_order
    ("leader", "Leader", "قائد", 1, 1),
    ("vp", "VP", "نائب", 1, 2),
    ("member", "Member", "عضو", None, 3),
]
ROLE_BY_ASSIGNMENT = {"leader": "leader", "deputy": "vp", "member": "member", "president": "leader"}

# The Board was renamed to this by 8c9211f55b8b; used once here to set its
# ranking flag, never at runtime.
BOARD_NAME = "Board of Directors"
LEADERSHIP = {"name": "Leadership", "ar_name": "القادة", "type": "administrative", "icon": "network"}


def _current_semester_id(conn) -> str | None:
    """The semester the calendar rule calls current - see app/semesters.py."""
    today = datetime.now(ZoneInfo("Asia/Riyadh")).date()
    return conn.execute(
        sa.text("SELECT id FROM semesters WHERE start_date <= :today ORDER BY start_date DESC LIMIT 1"),
        {"today": today},
    ).scalar()


def _audit(conn) -> dict:
    """Every check, before any DDL. Returns what the data steps need."""
    problems: list[str] = []

    current_id = _current_semester_id(conn)
    open_rows = conn.execute(
        sa.text(
            "SELECT id, member_id, department_id, role, starts_at, changed_by "
            "FROM club_assignments WHERE ends_at IS NULL ORDER BY id"
        )
    ).all()
    if open_rows and current_id is None:
        problems.append(f"{len(open_rows)} current club assignments but no semester has started yet")

    presidents = [row for row in open_rows if row.role == "president"]
    if len(presidents) > 2:
        problems.append(f"{len(presidents)} current presidents; at most 2 fit the Leadership department")

    boards = conn.execute(sa.text("SELECT id FROM departments WHERE name = :name"), {"name": BOARD_NAME}).all()
    if len(boards) > 1:
        problems.append(f"{len(boards)} departments are named {BOARD_NAME!r}; expected at most one")

    taken = conn.execute(
        sa.text("SELECT id FROM departments WHERE name = :name"), {"name": LEADERSHIP["name"]}
    ).scalar()
    if taken is not None:
        problems.append(f"department {taken} is already named {LEADERSHIP['name']!r}")

    if problems:
        raise RuntimeError(
            "Club structure migration aborted before any change was made:\n  - " + "\n  - ".join(problems)
        )
    return {"current_id": current_id, "open_rows": open_rows, "board_id": boards[0].id if boards else None}


def upgrade() -> None:
    conn = op.get_bind()
    audit = _audit(conn)
    current_id = audit["current_id"]

    # 1. Departments: the ranking flag replaces the Board name check, and the
    #    Leadership department is marked explicitly. At most one department
    #    can carry the mark: the generated key is NULL for every other row.
    op.add_column(
        "departments", sa.Column("show_in_leaderboard", mysql.TINYINT(1), nullable=False, server_default=sa.text("'1'"))
    )
    op.add_column(
        "departments", sa.Column("is_club_leadership", mysql.TINYINT(1), nullable=False, server_default=sa.text("'0'"))
    )
    op.execute(
        "ALTER TABLE departments ADD COLUMN club_leadership_key TINYINT UNSIGNED "
        "AS (CASE WHEN is_club_leadership = 1 THEN 1 END) STORED"
    )
    op.create_index("uq_departments_club_leadership", "departments", ["club_leadership_key"], unique=True)
    op.drop_column("departments", "leadership_enabled")
    if audit["board_id"] is not None:
        conn.execute(
            sa.text("UPDATE departments SET show_in_leaderboard = 0 WHERE id = :id"), {"id": audit["board_id"]}
        )
    conn.execute(
        sa.text(
            "INSERT INTO departments (name, ar_name, type, icon, active, show_in_leaderboard, is_club_leadership) "
            "VALUES (:name, :ar_name, :type, :icon, 1, 0, 1)"
        ),
        LEADERSHIP,
    )
    leadership_id = conn.execute(sa.text("SELECT id FROM departments WHERE is_club_leadership = 1")).scalar()

    # 2. Roles, as data.
    op.create_table(
        "club_roles",
        sa.Column("id", UUID_TYPE, primary_key=True),
        sa.Column("key", sa.String(32), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("ar_name", sa.String(100), nullable=False),
        sa.Column("max_holders", mysql.TINYINT(unsigned=True), nullable=True),
        sa.Column("sort_order", mysql.TINYINT(unsigned=True), nullable=False),
        sa.UniqueConstraint("key", name="uq_club_roles_key"),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    for key, name, ar_name, max_holders, sort_order in ROLES:
        conn.execute(
            sa.text(
                "INSERT INTO club_roles (id, `key`, name, ar_name, max_holders, sort_order) "
                "VALUES (UUID(), :key, :name, :ar_name, :max_holders, :sort_order)"
            ),
            {"key": key, "name": name, "ar_name": ar_name, "max_holders": max_holders, "sort_order": sort_order},
        )
    role_ids = dict(conn.execute(sa.text("SELECT `key`, id FROM club_roles")).tuples().all())

    op.create_table(
        "department_role_limits",
        sa.Column("department_id", DEPARTMENT_ID, nullable=False),
        sa.Column("role_id", UUID_TYPE, nullable=False),
        sa.Column("max_holders", mysql.TINYINT(unsigned=True), nullable=True),
        sa.PrimaryKeyConstraint("department_id", "role_id"),
        sa.ForeignKeyConstraint(
            ["department_id"], ["departments.id"], name="fk_department_role_limits_department", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["role_id"], ["club_roles.id"], name="fk_department_role_limits_role", ondelete="CASCADE"
        ),
    )
    conn.execute(
        sa.text("INSERT INTO department_role_limits (department_id, role_id, max_holders) VALUES (:d, :r, 2)"),
        {"d": leadership_id, "r": role_ids["leader"]},
    )

    # 3. Which departments existed in each semester. Deleting a semester takes
    #    its department list (and its change log, below) with it; the roster's
    #    RESTRICT key, and the API before it, refuse while it still has a roster.
    op.create_table(
        "semester_departments",
        sa.Column("semester_id", UUID_TYPE, nullable=False),
        sa.Column("department_id", DEPARTMENT_ID, nullable=False),
        sa.Column("name", sa.String(50), nullable=True),
        sa.Column("ar_name", mysql.VARCHAR(100, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"), nullable=True),
        sa.PrimaryKeyConstraint("semester_id", "department_id"),
        sa.ForeignKeyConstraint(
            ["semester_id"], ["semesters.id"], name="fk_semester_departments_semester", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["department_id"], ["departments.id"], name="fk_semester_departments_department", ondelete="RESTRICT"
        ),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_0900_ai_ci",
    )
    conn.execute(
        sa.text(
            "INSERT INTO semester_departments (semester_id, department_id) "
            "SELECT DISTINCT e.semester_id, dl.department_id "
            "FROM departments_logs dl JOIN logs l ON l.id = dl.log_id JOIN events e ON e.id = l.event_id "
            "WHERE e.status <> 'draft'"
        )
    )
    if current_id is not None:
        conn.execute(
            sa.text(
                "INSERT IGNORE INTO semester_departments (semester_id, department_id) "
                "SELECT :semester_id, id FROM departments WHERE active = 1"
            ),
            {"semester_id": current_id},
        )

    # 4. The roster.
    op.create_table(
        "club_memberships",
        sa.Column("id", UUID_TYPE, primary_key=True),
        sa.Column("semester_id", UUID_TYPE, nullable=False),
        sa.Column("department_id", DEPARTMENT_ID, nullable=False),
        sa.Column("member_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("role_id", UUID_TYPE, nullable=False),
        sa.Column("created_by", sa.String(255), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
        sa.ForeignKeyConstraint(
            ["semester_id", "department_id"],
            ["semester_departments.semester_id", "semester_departments.department_id"],
            name="fk_club_memberships_semester_department",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"], name="fk_club_memberships_member", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["role_id"], ["club_roles.id"], name="fk_club_memberships_role", ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "semester_id", "department_id", "member_id", "role_id", name="uq_club_memberships_member_role"
        ),
        sa.Index("ix_club_memberships_member", "member_id", "semester_id"),
        sa.Index("ix_club_memberships_role", "semester_id", "department_id", "role_id"),
    )

    # 5. Who changed what. Not tied to semester_departments, so the log
    #    outlives a department being taken out of a semester.
    op.create_table(
        "club_membership_changes",
        sa.Column("id", UUID_TYPE, primary_key=True),
        sa.Column("semester_id", UUID_TYPE, nullable=False),
        sa.Column("department_id", DEPARTMENT_ID, nullable=False),
        sa.Column("member_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("role_id", UUID_TYPE, nullable=False),
        sa.Column("action", sa.Enum("added", "removed"), nullable=False),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
        sa.ForeignKeyConstraint(
            ["semester_id"], ["semesters.id"], name="fk_club_membership_changes_semester", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["department_id"], ["departments.id"], name="fk_club_membership_changes_department", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["member_id"], ["members.id"], name="fk_club_membership_changes_member", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["role_id"], ["club_roles.id"], name="fk_club_membership_changes_role", ondelete="RESTRICT"
        ),
        sa.Index("ix_club_membership_changes_scope", "semester_id", "department_id", "created_at"),
        sa.Index("ix_club_membership_changes_member", "member_id", "created_at"),
    )

    # 6. The open assignments become the current semester's roster. Every
    #    leader, VP and president also gets an explicit member row. The log
    #    keeps the original actor and start time.
    if current_id is not None:
        conn.execute(
            sa.text("INSERT IGNORE INTO semester_departments (semester_id, department_id) VALUES (:s, :d)"),
            {"s": current_id, "d": leadership_id},
        )
    rows: set[tuple] = set()
    for row in audit["open_rows"]:
        department_id = leadership_id if row.role == "president" else row.department_id
        role = ROLE_BY_ASSIGNMENT[row.role]
        for key in {role, "member"}:
            if (department_id, row.member_id, key) in rows:
                continue
            rows.add((department_id, row.member_id, key))
            params = {
                "s": current_id,
                "d": department_id,
                "m": row.member_id,
                "r": role_ids[key],
                "by": row.changed_by,
                "at": row.starts_at,
            }
            conn.execute(
                sa.text("INSERT IGNORE INTO semester_departments (semester_id, department_id) VALUES (:s, :d)"), params
            )
            conn.execute(
                sa.text(
                    "INSERT INTO club_memberships (id, semester_id, department_id, member_id, role_id, created_by, created_at) "
                    "VALUES (UUID(), :s, :d, :m, :r, :by, :at)"
                ),
                params,
            )
            conn.execute(
                sa.text(
                    "INSERT INTO club_membership_changes "
                    "(id, semester_id, department_id, member_id, role_id, action, actor, created_at) "
                    "VALUES (UUID(), :s, :d, :m, :r, 'added', :by, :at)"
                ),
                params,
            )

    op.drop_table("club_assignments")


def downgrade() -> None:
    """Recreates ``club_assignments`` empty. Restoring its rows means restoring a backup."""
    conn = op.get_bind()

    op.create_table(
        "club_assignments",
        sa.Column("id", mysql.INTEGER(unsigned=True), primary_key=True, autoincrement=True),
        sa.Column("member_id", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("department_id", mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column("role", sa.Enum("president", "leader", "deputy", "member"), nullable=False),
        sa.Column("president_slot", mysql.TINYINT(unsigned=True), nullable=True),
        sa.Column("starts_at", mysql.DATETIME(fsp=6), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP(6)")),
        sa.Column("ends_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("changed_by", sa.String(255), nullable=False),
        sa.Column("ended_by", sa.String(255), nullable=True),
        sa.Column(
            "current_scope_id",
            mysql.INTEGER(unsigned=True),
            sa.Computed("CASE WHEN ends_at IS NULL THEN COALESCE(department_id, 0) END"),
            nullable=True,
        ),
        sa.Column(
            "current_leadership_role",
            sa.String(6),
            sa.Computed("CASE WHEN ends_at IS NULL AND role IN ('leader', 'deputy') THEN role END"),
            nullable=True,
        ),
        sa.Column(
            "current_president_slot",
            mysql.TINYINT(unsigned=True),
            sa.Computed("CASE WHEN ends_at IS NULL THEN president_slot END"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"], name="fk_club_assignments_member", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["department_id"], ["departments.id"], name="fk_club_assignments_department", ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            "(role = 'president' AND department_id IS NULL AND president_slot IS NOT NULL "
            "AND president_slot IN (1, 2)) OR "
            "(role IN ('leader', 'deputy', 'member') AND department_id IS NOT NULL AND president_slot IS NULL)",
            name="ck_club_assignments_scope",
        ),
        sa.CheckConstraint("ends_at IS NULL OR ends_at >= starts_at", name="ck_club_assignments_period"),
        sa.Index("uq_club_assignments_current_member", "current_scope_id", "member_id", unique=True),
        sa.Index("uq_club_assignments_current_leader", "current_scope_id", "current_leadership_role", unique=True),
        sa.Index("uq_club_assignments_current_president", "current_president_slot", unique=True),
        sa.Index("ix_club_assignments_department_period", "department_id", "ends_at"),
        sa.Index("ix_club_assignments_member_period", "member_id", "starts_at"),
    )

    op.drop_table("club_membership_changes")
    op.drop_table("club_memberships")
    op.drop_table("semester_departments")
    op.drop_table("department_role_limits")
    op.drop_table("club_roles")

    op.add_column(
        "departments", sa.Column("leadership_enabled", mysql.TINYINT(1), nullable=False, server_default=sa.text("'1'"))
    )
    # The Leadership department goes unless something already points at it.
    conn.execute(
        sa.text(
            "DELETE FROM departments WHERE is_club_leadership = 1 "
            "AND id NOT IN (SELECT department_id FROM departments_logs)"
        )
    )
    op.drop_index("uq_departments_club_leadership", table_name="departments")
    op.drop_column("departments", "club_leadership_key")
    op.drop_column("departments", "is_club_leadership")
    op.drop_column("departments", "show_in_leaderboard")
