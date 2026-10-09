"""merge public member name and soft delete submissions heads

Revision ID: 60a68031d24b
Revises: 750018c84549, a2d4f6b8c0e1
Create Date: 2026-10-09 02:46:05.960312

"""

from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "60a68031d24b"
down_revision: Union[str, Sequence[str], None] = ("750018c84549", "a2d4f6b8c0e1")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
