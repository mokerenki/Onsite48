"""Add runtime correlation columns and schedule policy JSON.

Revision ID: 20260912_17
Revises: 20260911_16
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260912_17"
down_revision = "20260911_16"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("browser_session_events", sa.Column("task_id", sa.UUID(), nullable=True))
    op.add_column("browser_session_events", sa.Column("run_id", sa.UUID(), nullable=True))
    op.add_column("browser_session_events", sa.Column("step_attempt_id", sa.UUID(), nullable=True))
    op.add_column("browser_session_events", sa.Column("bridge_command_id", sa.UUID(), nullable=True))
    op.create_foreign_key("fk_browser_session_events_task", "browser_session_events", "task_threads", ["task_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_browser_session_events_run", "browser_session_events", "agent_runs", ["run_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_browser_session_events_step", "browser_session_events", "agent_step_attempts", ["step_attempt_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_browser_session_events_command", "browser_session_events", "browser_bridge_commands", ["bridge_command_id"], ["id"], ondelete="SET NULL")
    for name, column in (
        ("ix_browser_session_events_task", "task_id"),
        ("ix_browser_session_events_run", "run_id"),
        ("ix_browser_session_events_step", "step_attempt_id"),
        ("ix_browser_session_events_command", "bridge_command_id"),
    ):
        op.create_index(name, "browser_session_events", [column])

    op.add_column("agent_runs", sa.Column("browser_session_id", sa.UUID(), nullable=True))
    op.create_foreign_key("fk_agent_runs_browser_session", "agent_runs", "browser_sessions", ["browser_session_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_agent_runs_browser_session", "agent_runs", ["browser_session_id"])

    op.add_column("agent_step_attempts", sa.Column("browser_session_id", sa.UUID(), nullable=True))
    op.create_foreign_key("fk_agent_steps_browser_session", "agent_step_attempts", "browser_sessions", ["browser_session_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_agent_steps_browser_session", "agent_step_attempts", ["browser_session_id"])

    op.add_column("browser_bridge_commands", sa.Column("run_id", sa.UUID(), nullable=True))
    op.add_column("browser_bridge_commands", sa.Column("step_attempt_id", sa.UUID(), nullable=True))
    op.add_column("browser_bridge_commands", sa.Column("session_id", sa.UUID(), nullable=True))
    op.add_column("browser_bridge_commands", sa.Column("sequence", sa.Integer(), nullable=True))
    op.add_column("browser_bridge_commands", sa.Column("idempotency_key", sa.String(length=128), nullable=True))
    op.create_foreign_key("fk_bridge_commands_run", "browser_bridge_commands", "agent_runs", ["run_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_bridge_commands_step", "browser_bridge_commands", "agent_step_attempts", ["step_attempt_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_bridge_commands_session", "browser_bridge_commands", "browser_sessions", ["session_id"], ["id"], ondelete="SET NULL")
    for name, column in (
        ("ix_bridge_commands_run", "run_id"),
        ("ix_bridge_commands_step", "step_attempt_id"),
        ("ix_bridge_commands_session", "session_id"),
        ("ix_bridge_commands_sequence", "sequence"),
        ("ix_bridge_commands_idempotency", "idempotency_key"),
    ):
        op.create_index(name, "browser_bridge_commands", [column])

    op.add_column("scheduled_agent_tasks", sa.Column("schedule_policy", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")))


def downgrade() -> None:
    op.drop_column("scheduled_agent_tasks", "schedule_policy")
    for name in ("ix_bridge_commands_idempotency", "ix_bridge_commands_sequence", "ix_bridge_commands_session", "ix_bridge_commands_step", "ix_bridge_commands_run"):
        op.drop_index(name, table_name="browser_bridge_commands")
    for name in ("fk_bridge_commands_session", "fk_bridge_commands_step", "fk_bridge_commands_run"):
        op.drop_constraint(name, "browser_bridge_commands", type_="foreignkey")
    for column in ("idempotency_key", "sequence", "session_id", "step_attempt_id", "run_id"):
        op.drop_column("browser_bridge_commands", column)
    op.drop_index("ix_agent_steps_browser_session", table_name="agent_step_attempts")
    op.drop_constraint("fk_agent_steps_browser_session", "agent_step_attempts", type_="foreignkey")
    op.drop_column("agent_step_attempts", "browser_session_id")
    op.drop_index("ix_agent_runs_browser_session", table_name="agent_runs")
    op.drop_constraint("fk_agent_runs_browser_session", "agent_runs", type_="foreignkey")
    op.drop_column("agent_runs", "browser_session_id")
    for name in ("ix_browser_session_events_command", "ix_browser_session_events_step", "ix_browser_session_events_run", "ix_browser_session_events_task"):
        op.drop_index(name, table_name="browser_session_events")
    for name in ("fk_browser_session_events_command", "fk_browser_session_events_step", "fk_browser_session_events_run", "fk_browser_session_events_task"):
        op.drop_constraint(name, "browser_session_events", type_="foreignkey")
    for column in ("bridge_command_id", "step_attempt_id", "run_id", "task_id"):
        op.drop_column("browser_session_events", column)


# Alembic's generated SQL needs PostgreSQL's UUID type, but SQLite-based unit tests
# only import this module and do not execute the migration.
SAFETY_NOTE = "All newly added correlation fields are nullable for backwards-compatible rollout."

__all__ = ["upgrade", "downgrade"]