"""create form_access_grants table

forms.admin_google_email only ever remembers the most recently granted
email - it's a single column, so when a second admin requests access to
the same form, it silently overwrites the record of the first admin's
grant even though their Drive permission was never touched. Both admins
genuinely have access; the database just couldn't represent more than
one of them, so the first admin's next visit showed a false "request
access" prompt for access they already had.

This table tracks every (form, email) pair actually granted, so the UI
can recognize any of them, not just the latest.

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-09-05 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql
from sqlalchemy.dialects.mysql import INTEGER

# revision identifiers, used by Alembic.
revision: str = "b3c4d5e6f7a8"
down_revision: Union[str, Sequence[str], None] = "a2b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "form_access_grants",
        sa.Column("id", INTEGER(unsigned=True), autoincrement=True, nullable=False),
        sa.Column("form_id", INTEGER(unsigned=True), nullable=False),
        sa.Column(
            "google_email", mysql.VARCHAR(150, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"), nullable=False
        ),
        sa.Column("granted_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["form_id"], ["forms.id"], name="form_access_grants_ibfk_1", ondelete="CASCADE", onupdate="CASCADE"
        ),
    )
    op.create_index(
        "form_access_grants_unique_form_email", "form_access_grants", ["form_id", "google_email"], unique=True
    )

    # Backfill: whoever a form is currently shown as shared with already has real
    # Drive access today - without this, every existing grantee gets forced
    # through "request access" once more despite never having lost it.
    op.execute(
        "INSERT INTO form_access_grants (form_id, google_email) "
        "SELECT id, LOWER(admin_google_email) FROM forms WHERE admin_google_email IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_index("form_access_grants_unique_form_email", table_name="form_access_grants")
    op.drop_table("form_access_grants")
