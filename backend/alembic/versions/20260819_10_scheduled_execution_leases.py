"""Add durable occurrence and lease metadata for scheduled executions.

Revision ID: 20260819_10
Revises: 20260819_09
Create Date: 2026-08-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260819_10"
down_revision = "20260819_09"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing rows were produced before occurrence identity existed. Their
    # started_at is the only durable, non-ambiguous approximation available.
    op.add_column(
        "scheduled_task_executions",
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "scheduled_task_executions",
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "scheduled_task_executions",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE scheduled_task_executions SET scheduled_for = started_at WHERE scheduled_for IS NULL")
    op.alter_column("scheduled_task_executions", "scheduled_for", nullable=False)
    op.create_index(
        "ix_scheduled_task_executions_scheduled_for",
        "scheduled_task_executions",
        ["scheduled_for"],
    )
    op.create_index(
        "ix_scheduled_task_executions_lease_expires_at",
        "scheduled_task_executions",
        ["lease_expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_scheduled_task_executions_lease_expires_at", table_name="scheduled_task_executions")
    op.drop_index("ix_scheduled_task_executions_scheduled_for", table_name="scheduled_task_executions")
    op.drop_column("scheduled_task_executions", "lease_expires_at")
    op.drop_column("scheduled_task_executions", "heartbeat_at")
    op.drop_column("scheduled_task_executions", "scheduled_for")