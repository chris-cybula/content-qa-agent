# Migration eba16b86c720
# Creates the findings table.

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "eba16b86c720"
down_revision: str | Sequence[str] | None = "e79c5dab2863"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "findings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("page_id", sa.Integer(), nullable=False),
        sa.Column("rule", sa.String(length=50), nullable=False),
        sa.Column("severity", sa.String(length=10), nullable=False),
        sa.Column("element", sa.Text(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("suggested_fix", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["page_id"], ["pages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(op.f("ix_findings_page_id"), "findings", ["page_id"], unique=False)
    op.create_index(op.f("ix_findings_run_id"), "findings", ["run_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_findings_run_id"), table_name="findings")
    op.drop_index(op.f("ix_findings_page_id"), table_name="findings")
    op.drop_table("findings")
