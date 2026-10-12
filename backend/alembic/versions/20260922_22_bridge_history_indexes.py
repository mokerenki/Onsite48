"""Add indexes for authenticated bridge status and history queries.

Revision ID: 20260922_22
Revises: 20260922_21
"""
from __future__ import annotations

from alembic import op

revision = "20260922_22"
down_revision = "20260922_21"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_bridge_devices_user_org_updated",
        "browser_bridge_devices",
        ["user_id", "organization_id", "updated_at"],
    )
    op.create_index(
        "ix_bridge_commands_user_org_created",
        "browser_bridge_commands",
        ["user_id", "organization_id", "created_at"],
    )
    op.create_index(
        "ix_bridge_rules_user_org_hostname",
        "browser_bridge_trust_rules",
        ["user_id", "organization_id", "hostname"],
    )


def downgrade() -> None:
    op.drop_index("ix_bridge_rules_user_org_hostname", table_name="browser_bridge_trust_rules")
    op.drop_index("ix_bridge_commands_user_org_created", table_name="browser_bridge_commands")
    op.drop_index("ix_bridge_devices_user_org_updated", table_name="browser_bridge_devices")