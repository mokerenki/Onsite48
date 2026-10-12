"""Carry a trace identifier from the request boundary to the task timeline.

Nothing established a trace at the boundary. `span()` minted an id lazily when
it found no current trace, so a single task that opened several spans could end
up with a different trace per span. The trace could not be reassembled, and
there was no identifier to give a user reporting "my task has been stuck for ten
minutes".

TaskEvent gains a trace_id so an event in the timeline can be walked back to the
spans and log lines that produced it, rather than correlated by timestamp alone.

Revision ID: 20260928_26
Revises: 20260928_25
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260928_26"
down_revision = "20260928_25"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable, with no server default: existing rows are events recorded before
    # traces existed, and inventing an id for them would be a fiction that
    # searches would then fail to find.
    op.add_column("task_events", sa.Column("trace_id", sa.String(64), nullable=True))
    op.create_index("ix_task_events_trace_id", "task_events", ["trace_id"])


def downgrade() -> None:
    op.drop_index("ix_task_events_trace_id", table_name="task_events")
    op.drop_column("task_events", "trace_id")