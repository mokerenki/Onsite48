"""Fence stale queue workers with per-claim lease tokens.

Revision ID: 20260929_28
Revises: 20260928_27
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260929_28"
down_revision = "20260928_27"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("task_threads", sa.Column("lease_token", sa.String(length=64), nullable=True))
    op.create_index("ix_task_threads_lease_token", "task_threads", ["lease_token"])


def downgrade() -> None:
    op.drop_index("ix_task_threads_lease_token", table_name="task_threads")
    op.drop_column("task_threads", "lease_token")