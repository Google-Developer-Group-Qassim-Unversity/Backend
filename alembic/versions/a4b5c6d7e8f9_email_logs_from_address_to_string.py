"""email_logs.from_address: enum -> plain string (drop Gmail-address enum for SES single-sender migration)

Revision ID: a4b5c6d7e8f9
Revises: f2a3b4c5d6e7
Create Date: 2026-08-24 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a4b5c6d7e8f9"
down_revision: Union[str, Sequence[str], None] = "f2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "email_logs",
        "from_address",
        existing_type=sa.Enum("info@kerneltics.com", "gdg.qu1@gmail.com"),
        type_=sa.String(255),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "email_logs",
        "from_address",
        existing_type=sa.String(255),
        type_=sa.Enum("info@kerneltics.com", "gdg.qu1@gmail.com"),
        existing_nullable=False,
    )
