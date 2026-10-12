"""Add an explicit partial-completion task status."""

from __future__ import annotations

from alembic import op

revision = "20261001_30"
down_revision = "20260929_29"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE task_status ADD VALUE IF NOT EXISTS 'partial'")
    op.create_unique_constraint("uq_agent_run_task_number", "agent_runs", ["task_id", "run_number"])


def downgrade() -> None:
    # PostgreSQL enum values cannot be removed safely in place. Older code can
    # continue treating this state as failed until the enum is rebuilt.
    op.drop_constraint("uq_agent_run_task_number", "agent_runs", type_="unique")