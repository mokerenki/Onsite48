"""Add durable agent runs, step attempts, evidence, and artifacts.

Revision ID: 20260904_15
Revises: 20260825_13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260904_15"
down_revision = "20260825_14"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("task_id", sa.UUID(), sa.ForeignKey("task_threads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(48), nullable=False, server_default="queued"),
        sa.Column("intent", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("plan", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agent_runs_task_id", "agent_runs", ["task_id"])
    op.create_index("ix_agent_runs_status", "agent_runs", ["status"])
    op.create_table(
        "agent_step_attempts",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("run_id", sa.UUID(), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_id", sa.String(96), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("tool_name", sa.String(160), nullable=False),
        sa.Column("arguments", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("status", sa.String(48), nullable=False, server_default="started"),
        sa.Column("result", sa.JSON()),
        sa.Column("error", sa.JSON()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("run_id", "idempotency_key", name="uq_agent_attempt_idempotency"),
    )
    op.create_index("ix_agent_step_attempts_run_id", "agent_step_attempts", ["run_id"])
    op.create_index("ix_agent_step_attempts_status", "agent_step_attempts", ["status"])
    for table, columns in (
        ("agent_evidence", [sa.Column("step_id", sa.String(96)), sa.Column("source_url", sa.String(2048)), sa.Column("title", sa.String(500)), sa.Column("content", sa.Text(), nullable=False), sa.Column("evidence_metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False)]),
        ("agent_artifacts", [sa.Column("filename", sa.String(255), nullable=False), sa.Column("media_type", sa.String(160), nullable=False), sa.Column("storage_key", sa.String(512), nullable=False, unique=True), sa.Column("checksum", sa.String(128), nullable=False), sa.Column("provenance", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False)]),
    ):
        op.create_table(table, sa.Column("id", sa.UUID(), primary_key=True), sa.Column("run_id", sa.UUID(), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False), *columns)
        op.create_index(f"ix_{table}_run_id", table, ["run_id"])


def downgrade() -> None:
    for table in ("agent_artifacts", "agent_evidence", "agent_step_attempts", "agent_runs"):
        op.drop_table(table)