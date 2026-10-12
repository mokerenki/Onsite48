"""Add durable Onsite48 Bridge evidence, verification, and cancellation fields.

Revision ID: 20260819_07
Revises: 20260817_06
Create Date: 2026-08-19
"""

from alembic import op
import sqlalchemy as sa


revision = "20260819_07"
down_revision = "20260817_06"
branch_labels = None
depends_on = None

JSON = sa.JSON()


def upgrade() -> None:
    op.add_column(
        "browser_bridge_commands",
        sa.Column("evidence", JSON, nullable=False, server_default=sa.text("'{}'::json")),
    )
    op.add_column(
        "browser_bridge_commands",
        sa.Column("verification", JSON, nullable=False, server_default=sa.text("'{}'::json")),
    )
    op.add_column(
        "browser_bridge_commands",
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "browser_bridge_commands",
        sa.Column("cancel_reason", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("browser_bridge_commands", "cancel_reason")
    op.drop_column("browser_bridge_commands", "cancelled_at")
    op.drop_column("browser_bridge_commands", "verification")
    op.drop_column("browser_bridge_commands", "evidence")