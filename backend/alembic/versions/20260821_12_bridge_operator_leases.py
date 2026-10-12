"""Add Bridge operator command leases for takeover-safe completion.

Revision ID: 20260821_12
Revises: 20260819_11
Create Date: 2026-08-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260821_12"
down_revision = "20260819_11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("browser_bridge_commands", sa.Column("lease_token", sa.String(length=128), nullable=True))
    op.add_column("browser_bridge_commands", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_browser_bridge_commands_lease_token", "browser_bridge_commands", ["lease_token"])
    op.create_index("ix_browser_bridge_commands_lease_expires_at", "browser_bridge_commands", ["lease_expires_at"])


def downgrade() -> None:
    op.drop_index("ix_browser_bridge_commands_lease_expires_at", table_name="browser_bridge_commands")
    op.drop_index("ix_browser_bridge_commands_lease_token", table_name="browser_bridge_commands")
    op.drop_column("browser_bridge_commands", "lease_expires_at")
    op.drop_column("browser_bridge_commands", "lease_token")