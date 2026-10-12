"""Persist artifact versions and lineage on canonical artifact rows.

Revision ID: 20260929_29
Revises: 20260929_28
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260929_29"
down_revision = "20260929_28"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_artifacts", sa.Column("logical_id", sa.String(length=255), nullable=False, server_default=""))
    op.add_column("agent_artifacts", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("agent_artifacts", sa.Column("parent_artifact_id", sa.UUID(), nullable=True))
    op.create_index("ix_agent_artifacts_logical_id", "agent_artifacts", ["logical_id"])
    op.create_index("ix_agent_artifacts_parent_artifact_id", "agent_artifacts", ["parent_artifact_id"])
    op.create_foreign_key("fk_agent_artifacts_parent_artifact_id", "agent_artifacts", "agent_artifacts", ["parent_artifact_id"], ["id"], ondelete="SET NULL")
    op.alter_column("agent_artifacts", "logical_id", server_default=None)
    op.alter_column("agent_artifacts", "version", server_default=None)


def downgrade() -> None:
    op.drop_constraint("fk_agent_artifacts_parent_artifact_id", "agent_artifacts", type_="foreignkey")
    op.drop_index("ix_agent_artifacts_parent_artifact_id", table_name="agent_artifacts")
    op.drop_index("ix_agent_artifacts_logical_id", table_name="agent_artifacts")
    op.drop_column("agent_artifacts", "parent_artifact_id")
    op.drop_column("agent_artifacts", "version")
    op.drop_column("agent_artifacts", "logical_id")