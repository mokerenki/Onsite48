"""Add a lease to task_threads so orphaned accepted work can be detected.

REST task acceptance dispatches through FastAPI BackgroundTasks, which run in
the accepting process after the response is sent. If that process restarts the
work is silently lost and the row stays ``queued`` forever: the user sees a task
that never progresses, with no terminal state and nothing to alert on.

A lease gives the row an expiry that a sweeper can compare against, so lost
work is failed visibly with a reason instead of hanging.

Revision ID: 20260927_23
Revises: 20260922_22
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260927_23"
down_revision = "20260922_22"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "task_threads",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_task_threads_lease_expires_at", "task_threads", ["lease_expires_at"])


def downgrade() -> None:
    op.drop_index("ix_task_threads_lease_expires_at", table_name="task_threads")
    op.drop_column("task_threads", "lease_expires_at")