"""Add custom connectors and durable scheduled execution records.

Revision ID: 20260819_08
Revises: 20260819_07
Create Date: 2026-08-19
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260819_08"
down_revision = "20260819_07"
branch_labels = None
depends_on = None

JSON = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "custom_connectors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("base_url", sa.String(length=2048), nullable=False),
        sa.Column("health_path", sa.String(length=1024), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("usage_guide", sa.Text(), nullable=True),
        sa.Column("configuration", JSON, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("encrypted_credentials", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="configured"),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("organization_id", "user_id", "slug", name="uq_custom_connector_owner_slug"),
    )
    op.create_index("ix_custom_connectors_organization_id", "custom_connectors", ["organization_id"])
    op.create_index("ix_custom_connectors_user_id", "custom_connectors", ["user_id"])
    op.create_index("ix_custom_connectors_slug", "custom_connectors", ["slug"])
    op.create_index("ix_custom_connectors_kind", "custom_connectors", ["kind"])
    op.create_index("ix_custom_connectors_status", "custom_connectors", ["status"])

    op.create_table(
        "scheduled_task_executions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("schedule_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("scheduled_agent_tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_thread_id", sa.String(length=128), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="running"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_scheduled_task_executions_schedule_id", "scheduled_task_executions", ["schedule_id"])
    op.create_index("ix_scheduled_task_executions_organization_id", "scheduled_task_executions", ["organization_id"])
    op.create_index("ix_scheduled_task_executions_user_id", "scheduled_task_executions", ["user_id"])
    op.create_index("ix_scheduled_task_executions_task_thread_id", "scheduled_task_executions", ["task_thread_id"])
    op.create_index("ix_scheduled_task_executions_status", "scheduled_task_executions", ["status"])


def downgrade() -> None:
    op.drop_index("ix_scheduled_task_executions_status", table_name="scheduled_task_executions")
    op.drop_index("ix_scheduled_task_executions_task_thread_id", table_name="scheduled_task_executions")
    op.drop_index("ix_scheduled_task_executions_user_id", table_name="scheduled_task_executions")
    op.drop_index("ix_scheduled_task_executions_organization_id", table_name="scheduled_task_executions")
    op.drop_index("ix_scheduled_task_executions_schedule_id", table_name="scheduled_task_executions")
    op.drop_table("scheduled_task_executions")
    op.drop_index("ix_custom_connectors_status", table_name="custom_connectors")
    op.drop_index("ix_custom_connectors_kind", table_name="custom_connectors")
    op.drop_index("ix_custom_connectors_slug", table_name="custom_connectors")
    op.drop_index("ix_custom_connectors_user_id", table_name="custom_connectors")
    op.drop_index("ix_custom_connectors_organization_id", table_name="custom_connectors")
    op.drop_table("custom_connectors")