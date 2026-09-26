"""semester uuid, hijri/gregorian codes, and events.semester_id

Revision ID: d1e2f3a4b5c6
Revises: 8c9211f55b8b
Create Date: 2026-09-27 00:00:00.000000

Makes the semester a real row that other tables point at.

``semesters``: the primary key was the university's term code (471), typed in
by an admin. It becomes a UUID, and the term is described by three facts - the
term (first / second / summer), the Hijri year (1447) and the Gregorian year
the academic year starts in (2025). MySQL derives both codes and the display
name from those, so a code can never disagree with its term:

    hijri_code      471 / 472 / 475    Hijri year's last two digits + 1 / 2 / 5
    gregorian_code  251 / 252 / 253    academic start year's last two digits + 1 / 2 / 3
    name            Fall 2025 / Spring 2026 / Summer 2026

``is_current`` is dropped: the current semester is worked out from the
calendar (see ``app/semesters.py``).

``events.semester_id``: every event belongs to exactly one semester - the most
recent one that had started by the event's end date. The time between two
terms therefore belongs to the term that just ended, never the next one.

MySQL cannot roll back DDL, so every check runs before the first ALTER. A
failed check leaves the database untouched.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, Sequence[str], None] = "8c9211f55b8b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Every UUID column shares one charset/collation: MySQL foreign keys require both
# sides to match, and the two tables do not share a default charset.
UUID_TYPE = mysql.CHAR(36, charset="ascii", collation="ascii_bin")

TERM_BY_DIGIT = {1: "first", 2: "second", 5: "summer"}

# Kept in step with app/DB/schema.py - the model declares the same expressions.
HIJRI_CODE_SQL = "(hijri_year % 100) * 10 + CASE term WHEN 'first' THEN 1 WHEN 'second' THEN 2 ELSE 5 END"
GREGORIAN_CODE_SQL = "(academic_year_start % 100) * 10 + CASE term WHEN 'first' THEN 1 WHEN 'second' THEN 2 ELSE 3 END"
NAME_SQL = (
    "CONCAT(CASE term WHEN 'first' THEN 'Fall' WHEN 'second' THEN 'Spring' ELSE 'Summer' END, ' ', "
    "academic_year_start + (term <> 'first'))"
)


def _audit(conn) -> list[dict]:
    """Every check, before any DDL. Returns the backfill for each semester row."""
    problems: list[str] = []

    semesters = conn.execute(sa.text("SELECT id, start_date, end_date FROM semesters ORDER BY start_date")).all()
    backfill = []
    for semester_id, start_date, _end_date in semesters:
        term = TERM_BY_DIGIT.get(semester_id % 10)
        if term is None or not 100 <= semester_id <= 999:
            problems.append(f"semester {semester_id}: not a <year><year><1|2|5> term code")
            continue
        hijri_year = 1400 + semester_id // 10
        # The first term starts in the autumn of the academic start year; the
        # second term and the summer start in the calendar year after it.
        academic_year_start = start_date.year if term == "first" else start_date.year - 1
        backfill.append(
            {"old_id": semester_id, "term": term, "hijri_year": hijri_year, "academic_year_start": academic_year_start}
        )

    overlaps = conn.execute(
        sa.text(
            "SELECT a.id, b.id FROM semesters a JOIN semesters b "
            "ON a.id < b.id AND a.start_date <= b.end_date AND a.end_date >= b.start_date"
        )
    ).all()
    problems += [f"semesters {a} and {b} overlap" for a, b in overlaps]

    event_count = conn.execute(sa.text("SELECT COUNT(*) FROM events")).scalar()
    if event_count and not semesters:
        problems.append(f"{event_count} events exist but no semesters are defined")

    orphans = conn.execute(
        sa.text(
            "SELECT id, end_datetime FROM events "
            "WHERE DATE(end_datetime) < (SELECT MIN(start_date) FROM semesters) ORDER BY end_datetime"
        )
    ).all()
    problems += [f"event {event_id} ends {end} - before the oldest semester" for event_id, end in orphans]

    if problems:
        raise RuntimeError(
            "Semester migration aborted before any change was made:\n  - "
            + "\n  - ".join(problems)
            + "\nAdd or fix the semesters in the admin app, then run the migration again."
        )
    return backfill


def upgrade() -> None:
    conn = op.get_bind()
    backfill = _audit(conn)

    # 1. The three facts a semester is described by, plus the future primary key.
    op.add_column("semesters", sa.Column("uuid", UUID_TYPE, nullable=True))
    op.add_column("semesters", sa.Column("term", sa.Enum("first", "second", "summer"), nullable=True))
    op.add_column("semesters", sa.Column("hijri_year", mysql.SMALLINT(unsigned=True), nullable=True))
    op.add_column("semesters", sa.Column("academic_year_start", mysql.SMALLINT(unsigned=True), nullable=True))
    for row in backfill:
        conn.execute(
            sa.text(
                "UPDATE semesters SET uuid = UUID(), term = :term, hijri_year = :hijri_year, "
                "academic_year_start = :academic_year_start WHERE id = :old_id"
            ),
            row,
        )
    op.alter_column("semesters", "uuid", existing_type=UUID_TYPE, nullable=False)
    op.alter_column("semesters", "term", existing_type=sa.Enum("first", "second", "summer"), nullable=False)
    op.alter_column("semesters", "hijri_year", existing_type=mysql.SMALLINT(unsigned=True), nullable=False)
    op.alter_column("semesters", "academic_year_start", existing_type=mysql.SMALLINT(unsigned=True), nullable=False)

    # 2. Codes and name, derived by MySQL. `name` replaces the hand-typed column.
    op.drop_column("semesters", "name")
    op.execute(
        f"ALTER TABLE semesters "
        f"ADD COLUMN hijri_code SMALLINT UNSIGNED AS ({HIJRI_CODE_SQL}) STORED NOT NULL AFTER academic_year_start, "
        f"ADD COLUMN gregorian_code SMALLINT UNSIGNED AS ({GREGORIAN_CODE_SQL}) STORED NOT NULL AFTER hijri_code, "
        f"ADD COLUMN name VARCHAR(20) AS ({NAME_SQL}) STORED NOT NULL AFTER gregorian_code"
    )
    mismatched = conn.execute(sa.text("SELECT id, hijri_code FROM semesters WHERE hijri_code <> id")).all()
    if mismatched:  # the audit derived these, so this would be a bug in this file
        raise RuntimeError(f"hijri_code does not reproduce the old id: {mismatched}")

    # 3. Every event gets the most recent semester that had started by its end date.
    op.add_column("events", sa.Column("semester_id", UUID_TYPE, nullable=True))
    conn.execute(
        sa.text(
            "UPDATE events e SET e.semester_id = ("
            "  SELECT s.uuid FROM semesters s WHERE s.start_date <= DATE(e.end_datetime)"
            "  ORDER BY s.start_date DESC LIMIT 1)"
        )
    )
    unassigned = conn.execute(sa.text("SELECT COUNT(*) FROM events WHERE semester_id IS NULL")).scalar()
    if unassigned:
        raise RuntimeError(f"{unassigned} events were left without a semester")

    # 4. The UUID becomes the primary key; the old code-as-id and the flag go.
    op.execute("ALTER TABLE semesters DROP PRIMARY KEY")
    op.drop_column("semesters", "id")
    op.drop_column("semesters", "is_current")
    op.alter_column("semesters", "uuid", new_column_name="id", existing_type=UUID_TYPE, existing_nullable=False)
    op.create_primary_key("pk_semesters", "semesters", ["id"])
    op.create_index("uq_semesters_hijri_code", "semesters", ["hijri_code"], unique=True)
    op.create_index("uq_semesters_gregorian_code", "semesters", ["gregorian_code"], unique=True)
    op.create_index("uq_semesters_hijri_year_term", "semesters", ["hijri_year", "term"], unique=True)
    op.create_check_constraint("ck_semesters_dates", "semesters", "end_date >= start_date")

    op.alter_column("events", "semester_id", existing_type=UUID_TYPE, nullable=False)
    op.create_index("ix_events_semester_start", "events", ["semester_id", "start_datetime"])
    op.create_foreign_key("fk_events_semester", "events", "semesters", ["semester_id"], ["id"], ondelete="RESTRICT")


def downgrade() -> None:
    conn = op.get_bind()

    op.drop_constraint("fk_events_semester", "events", type_="foreignkey")
    op.drop_index("ix_events_semester_start", table_name="events")
    op.drop_column("events", "semester_id")

    op.drop_constraint("ck_semesters_dates", "semesters", type_="check")
    op.drop_index("uq_semesters_hijri_year_term", table_name="semesters")
    op.drop_index("uq_semesters_gregorian_code", table_name="semesters")
    op.drop_index("uq_semesters_hijri_code", table_name="semesters")

    op.add_column("semesters", sa.Column("old_id", mysql.INTEGER(unsigned=True), nullable=True))
    op.add_column("semesters", sa.Column("old_name", sa.VARCHAR(100), nullable=True))
    op.add_column("semesters", sa.Column("is_current", mysql.TINYINT(1), nullable=False, server_default=sa.text("'0'")))
    conn.execute(sa.text("UPDATE semesters SET old_id = hijri_code, old_name = name"))
    # The flag goes back on the semester the calendar rule picks today.
    conn.execute(
        sa.text(
            "UPDATE semesters SET is_current = 1 WHERE start_date = ("
            "  SELECT d FROM (SELECT MAX(start_date) AS d FROM semesters WHERE start_date <= CURDATE()) latest)"
        )
    )

    op.execute("ALTER TABLE semesters DROP PRIMARY KEY")
    op.drop_column("semesters", "id")
    op.drop_column("semesters", "name")
    op.drop_column("semesters", "gregorian_code")
    op.drop_column("semesters", "hijri_code")
    op.drop_column("semesters", "academic_year_start")
    op.drop_column("semesters", "hijri_year")
    op.drop_column("semesters", "term")
    op.alter_column(
        "semesters", "old_id", new_column_name="id", existing_type=mysql.INTEGER(unsigned=True), nullable=False
    )
    op.alter_column("semesters", "old_name", new_column_name="name", existing_type=sa.VARCHAR(100))
    op.create_primary_key("PRIMARY", "semesters", ["id"])
