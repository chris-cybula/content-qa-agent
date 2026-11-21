# Migration c3f1a9d27b40
# Adds cost_usd to claude_calls: each call's cost in dollars.

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3f1a9d27b40"
down_revision: str | Sequence[str] | None = "a174902f0ed1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "claude_calls",
        sa.Column(
            "cost_usd",
            sa.Numeric(precision=10, scale=4),
            sa.Computed("round(cost_microdollars / 1000000.0, 4)", persisted=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("claude_calls", "cost_usd")
