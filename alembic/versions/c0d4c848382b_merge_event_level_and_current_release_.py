"""merge event level and current release migrations

Revision ID: c0d4c848382b
Revises: 2f8c7b6a901d, b3e7c1d9a5f2
Create Date: 2026-11-08 13:41:18.102484

"""

from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "c0d4c848382b"
down_revision: Union[str, Sequence[str], None] = ("2f8c7b6a901d", "b3e7c1d9a5f2")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
