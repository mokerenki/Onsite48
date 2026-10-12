"""Add durable schedule-level delegation policy.

Revision ID: 20260825_13
Revises: 20260821_12
Create Date: 2026-08-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260825_13"
down_revision = "20260821_12"
branch_labels = None
depends_on = None


_DEFAULT_POLICY = '{"mode":"approval_required","additional_allowed_tools":[],"allowed_custom_mcp_tools":[]}'


def upgrade() -> None:
    op.add_column(
        "scheduled_agent_tasks",
        sa.Column(
            "autonomy_policy",
            sa.JSON(),
            nullable=False,
            server_default=sa.text(f"'{_DEFAULT_POLICY}'"),
        ),
    )
    # Keep the historic schedules approval-first while allowing the application
    # to write explicit policy objects on every new or updated schedule.
    op.alter_column("scheduled_agent_tasks", "autonomy_policy", server_default=None)


def downgrade() -> None:
    op.drop_column("scheduled_agent_tasks", "autonomy_policy")