# Migration f4b2c8d1a7e3
# Creates the fix_proposals table: agent fixes waiting for a person's review.

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f4b2c8d1a7e3"
down_revision: str | Sequence[str] | None = "d81e5c3a9f12"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fix_proposals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("finding_id", sa.Integer(), nullable=False),
        sa.Column("new_text", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["finding_id"], ["findings.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        op.f("ix_fix_proposals_finding_id"), "fix_proposals", ["finding_id"], unique=False
    )
    op.create_index(op.f("ix_fix_proposals_run_id"), "fix_proposals", ["run_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_fix_proposals_run_id"), table_name="fix_proposals")
    op.drop_index(op.f("ix_fix_proposals_finding_id"), table_name="fix_proposals")
    op.drop_table("fix_proposals")
