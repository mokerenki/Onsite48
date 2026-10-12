"""Add the index used by the authenticated recent-task sidebar query.

Revision ID: 20260922_21
Revises: 20260922_20
"""
from __future__ import annotations

from alembic import op

revision = "20260922_21"
down_revision = "20260922_20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_task_threads_user_org_updated",
        "task_threads",
        ["user_id", "organization_id", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_task_threads_user_org_updated", table_name="task_threads")