"""redesign forms google auth

Forms used to be attached by copying a template into each admin's own Google
Drive via their own OAuth grant, so google_refresh_token held a per-event,
per-admin token - stored in plaintext, alongside the same token living in the
frontend's localStorage/cookies. The replacement design has one club-owned
Google account (its refresh token lives in Infisical as GOOGLE_REFRESH_TOKEN,
see docs/GOOGLE_FORMS.md) create and own every form, inviting the admin as a
Drive editor instead. There is nothing per-event left to store for auth, so
the column is dropped outright (hard cutover - no migration path for forms
attached under the old system). admin_google_email replaces it, recording
which address the form was last shared with, for support/audit only.

Revision ID: a2b3c4d5e6f7
Revises: e0f1a2b3c4d5
Create Date: 2026-09-04 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision: str = "a2b3c4d5e6f7"
down_revision: Union[str, Sequence[str], None] = "e0f1a2b3c4d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("forms", "google_refresh_token")
    op.add_column(
        "forms",
        sa.Column(
            "admin_google_email", mysql.VARCHAR(150, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"), nullable=True
        ),
    )


def downgrade() -> None:
    op.drop_column("forms", "admin_google_email")
    op.add_column(
        "forms",
        sa.Column(
            "google_refresh_token", mysql.VARCHAR(500, charset="utf8mb4", collation="utf8mb4_0900_ai_ci"), nullable=True
        ),
    )
