"""Add organization-scoped marketing campaign board.

Revision ID: 20260819_09
Revises: 20260819_08
Create Date: 2026-08-19
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260819_09"
down_revision = "20260819_08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "marketing_campaigns",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("channel", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="backlog"),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_marketing_campaigns_organization_id", "marketing_campaigns", ["organization_id"])
    op.create_index("ix_marketing_campaigns_created_by_user_id", "marketing_campaigns", ["created_by_user_id"])
    op.create_index("ix_marketing_campaigns_owner_user_id", "marketing_campaigns", ["owner_user_id"])
    op.create_index("ix_marketing_campaigns_status", "marketing_campaigns", ["status"])
    op.create_index("ix_marketing_campaigns_due_at", "marketing_campaigns", ["due_at"])


def downgrade() -> None:
    op.drop_index("ix_marketing_campaigns_due_at", table_name="marketing_campaigns")
    op.drop_index("ix_marketing_campaigns_status", table_name="marketing_campaigns")
    op.drop_index("ix_marketing_campaigns_owner_user_id", table_name="marketing_campaigns")
    op.drop_index("ix_marketing_campaigns_created_by_user_id", table_name="marketing_campaigns")
    op.drop_index("ix_marketing_campaigns_organization_id", table_name="marketing_campaigns")
    op.drop_table("marketing_campaigns")