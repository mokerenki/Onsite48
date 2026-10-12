"""Append-only run_events journal for the agent loop runtime.

Revision ID: 20261003_34
Revises: 20261002_33
"""
from __future__ import annotations
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20261003_34"
down_revision = "20261002_33"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        "run_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("run_id", UUID(as_uuid=True), nullable=False),
        sa.Column("parent_run_id", UUID(as_uuid=True), nullable=True),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("payload", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_run_events_run_id", "run_events", ["run_id"])
    op.create_index("ix_run_events_parent_run_id", "run_events", ["parent_run_id"])
    op.create_index("ix_run_events_type", "run_events", ["type"])
    op.create_index("ix_run_events_trace_id", "run_events", ["trace_id"])
    op.create_unique_constraint("uq_run_events_run_seq", "run_events", ["run_id", "seq"])

def downgrade() -> None:
    op.drop_constraint("uq_run_events_run_seq", "run_events", type_="unique")
    op.drop_index("ix_run_events_trace_id", table_name="run_events")
    op.drop_index("ix_run_events_type", table_name="run_events")
    op.drop_index("ix_run_events_parent_run_id", table_name="run_events")
    op.drop_index("ix_run_events_run_id", table_name="run_events")
    op.drop_table("run_events")
