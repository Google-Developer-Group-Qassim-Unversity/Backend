"""add public member name

Revision ID: 750018c84549
Revises: 9f7a8b9c0d1e
Create Date: 2026-10-04 13:46:48.297169

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import VARCHAR


# revision identifiers, used by Alembic.
revision: str = "750018c84549"
down_revision: Union[str, Sequence[str], None] = "9f7a8b9c0d1e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "members",
        sa.Column("public_name", VARCHAR(150, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"), nullable=True),
    )
    connection = op.get_bind()
    last_id = 0
    while True:
        rows = (
            connection.execute(
                sa.text("""
                SELECT m.id, m.name
                FROM members m
                WHERE m.id > :last_id ORDER BY m.id LIMIT 1000
            """),
                {"last_id": last_id},
            )
            .mappings()
            .all()
        )
        if not rows:
            break
        # Freeze the formatter here: future app changes must not alter old migrations.
        values = []
        for row in rows:
            # Wallet card names are independent; initialize only from the full name.
            parts = row["name"].split()
            if len(parts) > 2:
                family = parts[-2:] if parts[-1] == "الله" else parts[-1:]
                parts = [parts[0], *family]
            public_name = " ".join(parts) or "Member"
            values.append({"member_id": row["id"], "public_name": public_name})
        connection.execute(sa.text("UPDATE members SET public_name = :public_name WHERE id = :member_id"), values)
        last_id = rows[-1]["id"]
    op.alter_column(
        "members",
        "public_name",
        existing_type=VARCHAR(150, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"),
        nullable=False,
    )


def downgrade() -> None:
    op.drop_column("members", "public_name")
