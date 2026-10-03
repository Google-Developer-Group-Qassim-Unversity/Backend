"""uuid ids for the events pipeline and permission grants

Revision ID: 7d5e6f7a8b9c
Revises: 6c4d5e6f7a8b
Create Date: 2026-10-04 12:00:00.000000

Every new table gets a UUID id (CLAUDE.md, "Ids are UUIDs"). These were
created with auto-increment integers and are switched, keeping every row:

    event_requests          <- event_request_partners.request_id
                               event_request_tasks.request_id
                               pipeline_notifications.request_id
                               pipeline_penalties.request_id
    event_request_tasks
    pipeline_notifications  <- pipeline_notification_reads.notification_id
    pipeline_penalties
    permission_grants

Each row gets a fresh UUID and every reference is remapped to it, before any
column is dropped. Links that carry an old number (an email sent during the
trial, a bookmarked /pipeline/requests/12) stop working.

The older tables (members, events, departments, logs...) keep their integers:
too much depends on them, including the leaderboard app.

MySQL cannot roll back DDL. The checks run after the backfill and before the
first DROP, so a failed check leaves only the extra, unused columns behind.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7d5e6f7a8b9c"
down_revision: Union[str, Sequence[str], None] = "6c4d5e6f7a8b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID_SQL = "CHAR(36) CHARACTER SET ascii COLLATE ascii_bin"
INT_SQL = "INT UNSIGNED"

# Tables whose own id changes.
TABLES = ("event_requests", "event_request_tasks", "pipeline_notifications", "pipeline_penalties", "permission_grants")

# (table, column, the table it points at, foreign key name). All ON DELETE CASCADE.
REFERENCES = (
    ("event_request_partners", "request_id", "event_requests", "fk_event_request_partners_request"),
    ("event_request_tasks", "request_id", "event_requests", "fk_event_request_tasks_request"),
    ("pipeline_notifications", "request_id", "event_requests", "fk_pipeline_notifications_request"),
    ("pipeline_penalties", "request_id", "event_requests", "fk_pipeline_penalties_request"),
    (
        "pipeline_notification_reads",
        "notification_id",
        "pipeline_notifications",
        "fk_pipeline_notification_reads_notification",
    ),
)

# Keys that contain a referencing column: dropped before the swap, then rebuilt as they were.
PRIMARY_KEYS = {
    "event_request_partners": ("request_id", "department_id"),
    "pipeline_notification_reads": ("notification_id", "member_id"),
}
UNIQUE_INDEXES = (
    ("uq_event_request_tasks_team", "event_request_tasks", ("request_id", "team")),
    ("uq_pipeline_penalties_request", "pipeline_penalties", ("request_id",)),
)


def _swap(old_type: str, new_type: str, fill_id: str) -> None:
    conn = op.get_bind()

    # 1. New columns next to the old ones, filled in. Own ids first, then each
    #    reference through a join on the old ids, which still exist.
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ADD COLUMN new_id {new_type} NULL")
        op.execute(fill_id.format(table=table))
    for table, column, target, _fk in REFERENCES:
        op.execute(f"ALTER TABLE {table} ADD COLUMN new_{column} {new_type} NULL")
        op.execute(f"UPDATE {table} c JOIN {target} t ON t.id = c.{column} SET c.new_{column} = t.new_id")

    problems = []
    for table in TABLES:
        missing = conn.execute(sa.text(f"SELECT COUNT(*) FROM {table} WHERE new_id IS NULL")).scalar()
        if missing:
            problems.append(f"{table}: {missing} rows without a new id")
    for table, column, _target, _fk in REFERENCES:
        lost = conn.execute(sa.text(f"SELECT COUNT(*) FROM {table} WHERE new_{column} IS NULL")).scalar()
        if lost:
            problems.append(f"{table}.{column}: {lost} rows whose reference did not map")
    if problems:
        raise RuntimeError("Id migration stopped before dropping anything:\n  - " + "\n  - ".join(problems))

    # 2. Everything that holds the old columns.
    for table, _column, _target, fk in REFERENCES:
        op.drop_constraint(fk, table, type_="foreignkey")
    for name, table, _columns in UNIQUE_INDEXES:
        op.drop_index(name, table_name=table)
    for table in PRIMARY_KEYS:
        op.execute(f"ALTER TABLE {table} DROP PRIMARY KEY")

    # 3. The swap. An own id loses AUTO_INCREMENT before its primary key can go.
    for table, column, _target, _fk in REFERENCES:
        op.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        op.execute(f"ALTER TABLE {table} CHANGE COLUMN new_{column} {column} {new_type} NOT NULL")
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} MODIFY COLUMN id {old_type} NOT NULL")
        op.execute(f"ALTER TABLE {table} DROP PRIMARY KEY, DROP COLUMN id")
        op.execute(f"ALTER TABLE {table} CHANGE COLUMN new_id id {new_type} NOT NULL FIRST, ADD PRIMARY KEY (id)")

    # 4. Rebuild the keys, indexes and foreign keys as they were.
    for table, columns in PRIMARY_KEYS.items():
        op.execute(f"ALTER TABLE {table} ADD PRIMARY KEY ({', '.join(columns)})")
    for name, table, columns in UNIQUE_INDEXES:
        op.create_index(name, table, list(columns), unique=True)
    for table, column, target, fk in REFERENCES:
        op.create_foreign_key(fk, table, target, [column], ["id"], ondelete="CASCADE")


def upgrade() -> None:
    _swap(INT_SQL, UUID_SQL, fill_id="UPDATE {table} SET new_id = UUID()")


def downgrade() -> None:
    # Back to numbers 1..n per table, in id order. The integers the rows had
    # before the upgrade are gone; the links between rows are kept.
    _swap(
        UUID_SQL,
        INT_SQL,
        fill_id=(
            "UPDATE {table} t JOIN (SELECT id, ROW_NUMBER() OVER (ORDER BY id) AS n FROM {table}) x "
            "ON x.id = t.id SET t.new_id = x.n"
        ),
    )
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} MODIFY COLUMN id {INT_SQL} NOT NULL AUTO_INCREMENT")
