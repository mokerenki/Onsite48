"""Add scoped Onsite48 Bridge trusted-site permissions.

Revision ID: 20260815_05
Revises: 20260813_04
Create Date: 2026-08-15
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260815_05"
down_revision = "20260813_04"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSON = sa.JSON()


def upgrade() -> None:
    op.add_column(
        "browser_bridge_devices",
        sa.Column("active_context", JSON, nullable=False, server_default=sa.text("'{}'::json")),
    )
    op.create_table(
        "browser_bridge_trust_rules",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("device_id", UUID, sa.ForeignKey("browser_bridge_devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("hostname", sa.String(length=255), nullable=False),
        sa.Column("allowed_capabilities", JSON, nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("device_id", "hostname", name="uq_bridge_trust_rule_device_host"),
    )
    for column in ("device_id", "organization_id", "user_id", "hostname"):
        op.create_index(f"ix_bridge_trust_rules_{column}", "browser_bridge_trust_rules", [column])


def downgrade() -> None:
    op.drop_table("browser_bridge_trust_rules")
    op.drop_column("browser_bridge_devices", "active_context")