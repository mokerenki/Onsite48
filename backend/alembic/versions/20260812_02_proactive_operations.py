"""Create proactive operations tables.

Revision ID: 20260812_02
Revises: 20260812_01
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260812_02"
down_revision = "20260812_01"
branch_labels = None
depends_on = None

SIGNAL_STATUS = postgresql.ENUM("open", "dismissed", "resolved", name="proactive_signal_status", create_type=False)
PROPOSAL_STATUS = postgresql.ENUM("pending", "approved", "rejected", "executed", "failed", name="proposal_status", create_type=False)


def _is_offline() -> bool:
    return bool(op.get_context().as_sql)


def _has_table(table_name: str) -> bool:
    # Offline SQL represents a clean migration from this revision's predecessor;
    # no inspectable connection exists in that mode.
    if _is_offline():
        return False
    return sa.inspect(op.get_bind()).has_table(table_name)


def upgrade() -> None:
    # Every table is defined as it existed at this revision. Never call
    # current-ORM schema construction inside a historical migration: current fields
    # fields belong to later revisions and can make upgrade startup fail.
    if not _has_table("automation_subscriptions"):
        op.create_table(
            "automation_subscriptions",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
            sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("kind", sa.String(length=80), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("timezone", sa.String(length=64), nullable=False, server_default="UTC"),
            sa.Column("schedule", sa.JSON(), nullable=False),
            sa.Column("configuration", sa.JSON(), nullable=False),
            sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("organization_id", "user_id", "kind", name="uq_automation_subscription_kind"),
        )
        op.create_index("ix_automation_subscriptions_organization_id", "automation_subscriptions", ["organization_id"])
        op.create_index("ix_automation_subscriptions_user_id", "automation_subscriptions", ["user_id"])

    if not _has_table("proactive_signals"):
        SIGNAL_STATUS.create(op.get_bind(), checkfirst=True)
        op.create_table(
            "proactive_signals",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
            sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("source", sa.String(length=100), nullable=False),
            sa.Column("signal_type", sa.String(length=100), nullable=False),
            sa.Column("title", sa.String(length=300), nullable=False),
            sa.Column("summary", sa.Text(), nullable=False),
            sa.Column("severity", sa.String(length=24), nullable=False, server_default="medium"),
            sa.Column("confidence", sa.Integer(), nullable=False, server_default=sa.text("50")),
            sa.Column("deduplication_key", sa.String(length=255), nullable=False),
            sa.Column("evidence", sa.JSON(), nullable=False),
            sa.Column("status", SIGNAL_STATUS, nullable=False, server_default="open"),
            sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("organization_id", "deduplication_key", name="uq_proactive_signal_deduplication"),
        )
        op.create_index("ix_proactive_signals_organization_id", "proactive_signals", ["organization_id"])
        op.create_index("ix_proactive_signals_user_id", "proactive_signals", ["user_id"])
        op.create_index("ix_proactive_signals_severity", "proactive_signals", ["severity"])
        op.create_index("ix_proactive_signals_status", "proactive_signals", ["status"])
        op.create_index("ix_proactive_signals_detected_at", "proactive_signals", ["detected_at"])

    if not _has_table("action_proposals"):
        PROPOSAL_STATUS.create(op.get_bind(), checkfirst=True)
        op.create_table(
            "action_proposals",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
            sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("signal_id", sa.Uuid(), sa.ForeignKey("proactive_signals.id", ondelete="SET NULL"), nullable=True),
            sa.Column("connector_id", sa.String(length=100), nullable=True),
            sa.Column("action_type", sa.String(length=100), nullable=False),
            sa.Column("title", sa.String(length=300), nullable=False),
            sa.Column("rationale", sa.Text(), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("status", PROPOSAL_STATUS, nullable=False, server_default="pending"),
            sa.Column("approved_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("execution_result", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_action_proposals_organization_id", "action_proposals", ["organization_id"])
        op.create_index("ix_action_proposals_user_id", "action_proposals", ["user_id"])
        op.create_index("ix_action_proposals_signal_id", "action_proposals", ["signal_id"])
        op.create_index("ix_action_proposals_status", "action_proposals", ["status"])

    if not _has_table("morning_briefings"):
        op.create_table(
            "morning_briefings",
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
            sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("briefing_date", sa.String(length=10), nullable=False),
            sa.Column("summary", sa.Text(), nullable=False),
            sa.Column("signal_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("proposal_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("metadata_json", sa.JSON(), nullable=False),
            sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("organization_id", "user_id", "briefing_date", name="uq_morning_briefing_per_day"),
        )
        op.create_index("ix_morning_briefings_organization_id", "morning_briefings", ["organization_id"])
        op.create_index("ix_morning_briefings_user_id", "morning_briefings", ["user_id"])


def _drop_indexes_if_present(table_name: str, index_names: tuple[str, ...]) -> None:
    if _is_offline():
        for index_name in index_names:
            op.drop_index(index_name, table_name=table_name)
        return
    existing = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}
    for index_name in index_names:
        if index_name in existing:
            op.drop_index(index_name, table_name=table_name)


def downgrade() -> None:
    table_indexes = (
        ("morning_briefings", ("ix_morning_briefings_user_id", "ix_morning_briefings_organization_id")),
        ("action_proposals", ("ix_action_proposals_status", "ix_action_proposals_signal_id", "ix_action_proposals_user_id", "ix_action_proposals_organization_id")),
        ("proactive_signals", ("ix_proactive_signals_detected_at", "ix_proactive_signals_status", "ix_proactive_signals_severity", "ix_proactive_signals_user_id", "ix_proactive_signals_organization_id")),
        ("automation_subscriptions", ("ix_automation_subscriptions_user_id", "ix_automation_subscriptions_organization_id")),
    )
    for table_name, indexes in table_indexes:
        if _has_table(table_name) or _is_offline():
            _drop_indexes_if_present(table_name, indexes)
            op.drop_table(table_name)
    if not _is_offline():
        SIGNAL_STATUS.drop(op.get_bind(), checkfirst=True)
        PROPOSAL_STATUS.drop(op.get_bind(), checkfirst=True)