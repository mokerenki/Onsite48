"""Add durable user-controlled scheduled agent tasks.

Revision ID: 20260817_06
Revises: 20260815_05
Create Date: 2026-08-17
"""

from alembic import op
import sqlalchemy as sa


revision = "20260817_06"
down_revision = "20260815_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scheduled_agent_tasks",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("schedule_type", sa.String(length=24), nullable=False, server_default="once"),
        sa.Column("timezone", sa.String(length=64), nullable=False, server_default="UTC"),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_task_thread_id", sa.String(length=128), nullable=True),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_scheduled_agent_tasks_org", "scheduled_agent_tasks", ["organization_id"])
    op.create_index("ix_scheduled_agent_tasks_user", "scheduled_agent_tasks", ["user_id"])
    op.create_index("ix_scheduled_agent_tasks_run_at", "scheduled_agent_tasks", ["run_at"])
    op.create_index("ix_scheduled_agent_tasks_next_run_at", "scheduled_agent_tasks", ["next_run_at"])


def downgrade() -> None:
    op.drop_index("ix_scheduled_agent_tasks_next_run_at", table_name="scheduled_agent_tasks")
    op.drop_index("ix_scheduled_agent_tasks_run_at", table_name="scheduled_agent_tasks")
    op.drop_index("ix_scheduled_agent_tasks_user", table_name="scheduled_agent_tasks")
    op.drop_index("ix_scheduled_agent_tasks_org", table_name="scheduled_agent_tasks")
    op.drop_table("scheduled_agent_tasks")