"""Add durable queue priority to task threads."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261002_32"
down_revision = "20261001_31"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("task_threads", sa.Column("priority", sa.String(length=16), nullable=False, server_default="medium"))
    op.create_index("ix_task_threads_priority", "task_threads", ["priority"])


def downgrade() -> None:
    op.drop_index("ix_task_threads_priority", table_name="task_threads")
    op.drop_column("task_threads", "priority")