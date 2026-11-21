# Migration a174902f0ed1
# Creates the claude_calls table.

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a174902f0ed1"
down_revision: str | Sequence[str] | None = "eba16b86c720"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "claude_calls",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("purpose", sa.String(length=50), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("max_tokens", sa.Integer(), nullable=False),
        sa.Column("reserved_microdollars", sa.BigInteger(), nullable=False),
        sa.Column("cost_microdollars", sa.BigInteger(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=True),
        sa.Column("cache_write_tokens", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("request_id", sa.String(length=100), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        op.f("ix_claude_calls_created_at"), "claude_calls", ["created_at"], unique=False
    )
    op.create_index(op.f("ix_claude_calls_run_id"), "claude_calls", ["run_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_claude_calls_run_id"), table_name="claude_calls")
    op.drop_index(op.f("ix_claude_calls_created_at"), table_name="claude_calls")
    op.drop_table("claude_calls")
