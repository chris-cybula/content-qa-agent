# Migration d81e5c3a9f12
# Adds status to findings: open, approved or rejected.

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d81e5c3a9f12"
down_revision: str | Sequence[str] | None = "c3f1a9d27b40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "findings",
        sa.Column("status", sa.String(length=10), nullable=False, server_default="open"),
    )


def downgrade() -> None:
    op.drop_column("findings", "status")
