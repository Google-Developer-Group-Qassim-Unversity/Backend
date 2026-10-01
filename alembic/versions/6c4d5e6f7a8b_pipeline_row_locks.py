"""events pipeline: row locks instead of named locks

Revision ID: 6c4d5e6f7a8b
Revises: 5b3c4d5e6f7a
Create Date: 2026-10-01 20:00:00.000000

Booking and the sweep serialized themselves with ``GET_LOCK``, which belongs to
a database connection. The booking route commits inside the lock, the pool can
hand the release a different connection, and the lock then stayed held on the
pooled one, so every later booking waited 10 seconds and failed (bug #3).

``pipeline_locks`` has one row per lock, ``booking`` and ``sweep``. Locking a
row with ``SELECT ... FOR UPDATE`` ends with the transaction.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "6c4d5e6f7a8b"
down_revision: Union[str, Sequence[str], None] = "5b3c4d5e6f7a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    locks = op.create_table("pipeline_locks", sa.Column("name", sa.String(32), primary_key=True))
    op.bulk_insert(locks, [{"name": "booking"}, {"name": "sweep"}])


def downgrade() -> None:
    op.drop_table("pipeline_locks")
