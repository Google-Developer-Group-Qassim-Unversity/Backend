"""merge public names and soft-delete submissions

#151 and #154 both branched from 9f7a8b9c0d1e, so main had two heads and the
deploy's ``alembic upgrade head`` refused to run. Nothing to change; this only
joins them.

Revision ID: 6a947b802753
Revises: 750018c84549, a2d4f6b8c0e1
Create Date: 2026-10-09 03:33:22.731787

"""

from typing import Sequence, Union


# revision identifiers, used by Alembic.
revision: str = "6a947b802753"
down_revision: Union[str, Sequence[str], None] = ("750018c84549", "a2d4f6b8c0e1")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
