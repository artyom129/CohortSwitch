"""Record scheduled changes that could not be applied.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("scheduled_changes", sa.Column("failed_at", sa.DateTime(timezone=True)))
    op.add_column("scheduled_changes", sa.Column("last_error", sa.String(1000)))


def downgrade() -> None:
    op.drop_column("scheduled_changes", "last_error")
    op.drop_column("scheduled_changes", "failed_at")
