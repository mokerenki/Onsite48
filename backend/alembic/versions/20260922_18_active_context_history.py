"""Add active_context_history column to browser_bridge_devices.

Revision ID: 20260922_18
Revises: 20260912_17
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260922_18"
down_revision = "20260912_17"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("browser_bridge_devices", sa.Column("active_context_history", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")))


def downgrade() -> None:
    op.drop_column("browser_bridge_devices", "active_context_history")