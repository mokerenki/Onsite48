"""Repair missing OAuth account table from legacy foundation migration.

Revision ID: 20260825_14
Revises: 20260825_13
Create Date: 2026-08-25
"""

from alembic import op
import sqlalchemy as sa


revision = "20260825_14"
down_revision = "20260825_13"
branch_labels = None
depends_on = None


def _is_offline() -> bool:
    return bool(op.get_context().as_sql)


def _has_table(table_name: str) -> bool:
    # A static SQL script cannot know whether a target legacy database is
    # missing this table. Emit no ambiguous CREATE TABLE offline; perform this
    # state-sensitive repair through an online Alembic upgrade instead.
    if _is_offline():
        return True
    return sa.inspect(op.get_bind()).has_table(table_name)


def upgrade() -> None:
    """Create oauth_accounts only when a legacy installation is missing it.

    The original 20260812_01 revision delegated to current ORM metadata. Some
    installed databases were then stamped beyond that revision without this
    durable OAuth table, causing Google sign-in queries to fail with
    UndefinedTableError. This forward migration repairs only the missing table.
    """
    if _has_table("oauth_accounts"):
        return

    op.create_table(
        "oauth_accounts",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("provider_account_id", sa.String(length=255), nullable=False),
        sa.Column("encrypted_access_token", sa.Text(), nullable=True),
        sa.Column("encrypted_refresh_token", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider", "provider_account_id", name="uq_oauth_provider_account"),
    )
    op.create_index("ix_oauth_accounts_user_id", "oauth_accounts", ["user_id"])


def downgrade() -> None:
    # This is an intentionally forward-only, non-destructive repair migration.
    # The table may have existed before the repair on healthy installations, so
    # a generic downgrade cannot safely determine whether it owns the table.
    pass