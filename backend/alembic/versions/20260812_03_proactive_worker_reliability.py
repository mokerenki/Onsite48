"""Add retry and dead-letter state to proactive automations.

This revision supports both the intended historical schema and early deployments
where revision ``20260812_02`` created tables from live ORM metadata. The latter
may already contain one or more of these columns; upgrades must therefore be
idempotent rather than failing at backend startup.

Revision ID: 20260812_03
Revises: 20260812_02
Create Date: 2026-08-12
"""

from alembic import op
import sqlalchemy as sa


revision = "20260812_03"
down_revision = "20260812_02"
branch_labels = None
depends_on = None

TABLE_NAME = "automation_subscriptions"


def _is_offline() -> bool:
    return bool(op.get_context().as_sql)


def _column_names() -> set[str]:
    """Return live columns; offline SQL generation has no inspectable bind."""
    if _is_offline():
        return set()
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(TABLE_NAME)}


def _add_column_if_missing(column: sa.Column) -> None:
    if column.name not in _column_names():
        op.add_column(TABLE_NAME, column)


def _drop_column_if_present(column_name: str) -> None:
    if column_name in _column_names():
        op.drop_column(TABLE_NAME, column_name)


def upgrade() -> None:
    _add_column_if_missing(
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    _add_column_if_missing(sa.Column("last_error", sa.Text(), nullable=True))
    _add_column_if_missing(
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
    )
    _add_column_if_missing(
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    # Reversing this migration returns a clean historical schema to revision 02.
    # The checks preserve safe downgrade behavior for partially upgraded databases.
    _drop_column_if_present("dead_lettered_at")
    _drop_column_if_present("last_error_at")
    _drop_column_if_present("last_error")
    _drop_column_if_present("failure_count")