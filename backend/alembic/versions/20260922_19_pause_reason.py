"""Add pause_reason column to browser_bridge_devices.

Revision ID: 20260922_19_pause_reason
Revises: 20260922_18
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260922_19_pause_reason"
down_revision = "20260922_18"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("browser_bridge_devices", sa.Column("pause_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("browser_bridge_devices", "pause_reason")