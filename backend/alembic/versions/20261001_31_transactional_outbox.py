"""Add the transactional task-event outbox."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261001_31"
down_revision = "20261001_30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("event_id", sa.UUID(), sa.ForeignKey("task_events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.UUID(), sa.ForeignKey("task_threads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("topic", sa.String(length=160), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True)),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("event_id", name="uq_outbox_event_id"),
    )
    op.create_index("ix_outbox_events_event_id", "outbox_events", ["event_id"])
    op.create_index("ix_outbox_events_task_id", "outbox_events", ["task_id"])
    op.create_index("ix_outbox_events_topic", "outbox_events", ["topic"])
    op.create_index("ix_outbox_events_status", "outbox_events", ["status"])
    op.create_index("ix_outbox_events_available_at", "outbox_events", ["available_at"])


def downgrade() -> None:
    op.drop_table("outbox_events")