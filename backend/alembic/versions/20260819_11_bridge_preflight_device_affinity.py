"""Add Bridge device affinity, permission preflight, and claim leases.

Revision ID: 20260819_11
Revises: 20260819_10
Create Date: 2026-08-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260819_11"
down_revision = "20260819_10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "browser_bridge_devices",
        sa.Column("is_preferred", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_index("ix_browser_bridge_devices_is_preferred", "browser_bridge_devices", ["is_preferred"])

    op.add_column(
        "browser_bridge_commands",
        sa.Column("preflight_status", sa.String(length=32), nullable=False, server_default="waiting"),
    )
    op.add_column("browser_bridge_commands", sa.Column("preflight_origin", sa.String(length=2048), nullable=True))
    op.add_column("browser_bridge_commands", sa.Column("preflight_reason", sa.String(length=512), nullable=True))
    op.add_column("browser_bridge_commands", sa.Column("preflight_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("browser_bridge_commands", sa.Column("claim_lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_browser_bridge_commands_preflight_status", "browser_bridge_commands", ["preflight_status"])
    op.create_index("ix_browser_bridge_commands_claim_lease_expires_at", "browser_bridge_commands", ["claim_lease_expires_at"])


def downgrade() -> None:
    op.drop_index("ix_browser_bridge_commands_claim_lease_expires_at", table_name="browser_bridge_commands")
    op.drop_index("ix_browser_bridge_commands_preflight_status", table_name="browser_bridge_commands")
    op.drop_column("browser_bridge_commands", "claim_lease_expires_at")
    op.drop_column("browser_bridge_commands", "preflight_at")
    op.drop_column("browser_bridge_commands", "preflight_reason")
    op.drop_column("browser_bridge_commands", "preflight_origin")
    op.drop_column("browser_bridge_commands", "preflight_status")
    op.drop_index("ix_browser_bridge_devices_is_preferred", table_name="browser_bridge_devices")
    op.drop_column("browser_bridge_devices", "is_preferred")