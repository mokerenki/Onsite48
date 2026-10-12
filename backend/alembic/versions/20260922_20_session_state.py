"""Add session_state column to browser_bridge_devices.

Revision ID: 20260922_20
Revises: 20260922_19_pause_reason
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260922_20"
down_revision = "20260922_19_pause_reason"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "browser_bridge_devices",
        sa.Column(
            "session_state",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'AUTHORIZING'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("browser_bridge_devices", "session_state")