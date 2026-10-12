"""Add durable browser sessions and runtime event journal."""
from __future__ import annotations
import sqlalchemy as sa
from alembic import op
revision = "20260911_16"
down_revision = "20260904_15"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table("browser_sessions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("organization_id", sa.UUID(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.UUID(), sa.ForeignKey("task_threads.id", ondelete="SET NULL")),
        sa.Column("device_id", sa.UUID(), sa.ForeignKey("browser_bridge_devices.id", ondelete="SET NULL")),
        sa.Column("mode", sa.String(24), nullable=False, server_default="focused_tab"),
        sa.Column("status", sa.String(32), nullable=False, server_default="authorizing"),
        sa.Column("requested_origins", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("granted_capabilities", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("tab_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("primary_tab_id", sa.Integer()), sa.Column("current_step_id", sa.String(96)),
        sa.Column("current_url", sa.String(2048)), sa.Column("current_observation", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)), sa.Column("authorization_expires_at", sa.DateTime(timezone=True)), sa.Column("stop_reason", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_browser_sessions_org", "browser_sessions", ["organization_id"])
    op.create_index("ix_browser_sessions_user", "browser_sessions", ["user_id"])
    op.create_index("ix_browser_sessions_status", "browser_sessions", ["status"])
    op.create_table("browser_session_events",
        sa.Column("id", sa.UUID(), primary_key=True), sa.Column("session_id", sa.UUID(), sa.ForeignKey("browser_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False), sa.Column("event_type", sa.String(64), nullable=False), sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("session_id", "sequence", name="uq_browser_session_event_sequence"))
    op.create_index("ix_browser_session_events_session", "browser_session_events", ["session_id"])

def downgrade() -> None:
    op.drop_table("browser_session_events"); op.drop_table("browser_sessions")