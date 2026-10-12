"""Record non-idempotent tool calls so a replay does not act twice.

Revision ID: 20260928_25
Revises: 20260928_24
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260928_25"
down_revision = "20260928_24"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tool_execution_records",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.UUID(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("tool_name", sa.String(120), nullable=False),
        sa.Column("arguments_digest", sa.String(64), nullable=False),
        # Deliberately not a foreign key. A safety record deleted along with the
        # task protects nothing, and the record is written before the call, so
        # it must not depend on the task row existing.
        sa.Column("task_id", sa.UUID()),
        sa.Column("step_id", sa.String(128)),
        sa.Column(
            "status", sa.String(24), nullable=False, server_default="in_progress"
        ),
        sa.Column("result", sa.JSON()),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        # The uniqueness constraint is the whole mechanism: two concurrent
        # replays of the same step race to insert, and exactly one wins. Without
        # it a duplicate action is possible, which is the outcome this table
        # exists to prevent.
        sa.UniqueConstraint(
            "organization_id", "idempotency_key", name="uq_tool_execution_key"
        ),
    )
    op.create_index(
        "ix_tool_execution_records_organization_id",
        "tool_execution_records",
        ["organization_id"],
    )
    op.create_index(
        "ix_tool_execution_records_task_id", "tool_execution_records", ["task_id"]
    )
    op.create_index(
        "ix_tool_execution_records_status", "tool_execution_records", ["status"]
    )


def downgrade() -> None:
    op.drop_index("ix_tool_execution_records_status", table_name="tool_execution_records")
    op.drop_index("ix_tool_execution_records_task_id", table_name="tool_execution_records")
    op.drop_index(
        "ix_tool_execution_records_organization_id", table_name="tool_execution_records"
    )
    op.drop_table("tool_execution_records")