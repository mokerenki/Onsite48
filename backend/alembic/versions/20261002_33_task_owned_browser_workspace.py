"""Task-owned browser workspace: replace focused-tab as primary model.

Adds workspace ownership columns to browser_sessions and introduces the
browser_authorizations table for server-authoritative, task-scoped grants.

Revision ID: 20261002_33
Revises: 20261002_32
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "20261002_33"
down_revision = "20261002_32"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Expand browser_sessions with workspace ownership fields.
    op.add_column("browser_sessions", sa.Column("window_id", sa.Integer(), nullable=True))
    op.add_column("browser_sessions", sa.Column("tab_group_id", sa.Integer(), nullable=True))
    op.add_column("browser_sessions", sa.Column("tab_group_title", sa.String(length=128), nullable=True))
    op.add_column("browser_sessions", sa.Column("authorization_id", UUID(as_uuid=True), nullable=True))
    op.add_column("browser_sessions", sa.Column("world_revision", sa.Integer(), server_default="0", nullable=False))
    op.create_index("ix_browser_sessions_authorization_id", "browser_sessions", ["authorization_id"])

    # Change default mode for future inserts (existing rows keep their value).
    op.alter_column(
        "browser_sessions",
        "mode",
        existing_type=sa.String(length=24),
        server_default="task_workspace",
        existing_nullable=False,
    )

    # Server-authoritative authorization grants.
    op.create_table(
        "browser_authorizations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", UUID(as_uuid=True), sa.ForeignKey("task_threads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("device_id", UUID(as_uuid=True), sa.ForeignKey("browser_bridge_devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("session_id", UUID(as_uuid=True), sa.ForeignKey("browser_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("allowed_origins", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("allowed_capabilities", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_browser_authorizations_org", "browser_authorizations", ["organization_id"])
    op.create_index("ix_browser_authorizations_user", "browser_authorizations", ["user_id"])
    op.create_index("ix_browser_authorizations_task", "browser_authorizations", ["task_id"])
    op.create_index("ix_browser_authorizations_device", "browser_authorizations", ["device_id"])
    op.create_index("ix_browser_authorizations_session", "browser_authorizations", ["session_id"])
    op.create_index("ix_browser_authorizations_status", "browser_authorizations", ["status"])


def downgrade() -> None:
    op.drop_table("browser_authorizations")
    op.drop_index("ix_browser_sessions_authorization_id", table_name="browser_sessions")
    op.drop_column("browser_sessions", "world_revision")
    op.drop_column("browser_sessions", "authorization_id")
    op.drop_column("browser_sessions", "tab_group_title")
    op.drop_column("browser_sessions", "tab_group_id")
    op.drop_column("browser_sessions", "window_id")
    op.alter_column(
        "browser_sessions",
        "mode",
        existing_type=sa.String(length=24),
        server_default="focused_tab",
        existing_nullable=False,
    )
