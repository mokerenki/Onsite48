"""Dispatch retries: count the attempts on the task itself.

A lapsed lease means the process holding the task went away. Until now the only
response was to fail it, which is safe and also lossy: a deploy, a restart or a
crash between accepting a task and running it turned accepted work into a
visible failure, and the user had to ask again.

Knowing *how many* times a task has been dispatched is what turns that into a
decision. Below the limit the work is handed to another process; at the limit it
fails with a reason that says it failed because it kept losing its worker, which
is a different and more actionable thing for a user to read than "orphaned".

The count is on the task row rather than in a log, because the decision has to
be taken by a sweeper that may be the only process still running.

Revision ID: 20260928_27
Revises: 20260928_26
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260928_27"
down_revision = "20260928_26"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Server default so rows written by anything other than the ORM -- a manual
    # insert, a migration script, a row restored from a dump -- still get a
    # usable count rather than a NULL that every comparison has to special-case.
    op.add_column(
        "task_threads",
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_index("ix_task_threads_attempt_count", "task_threads", ["attempt_count"])


def downgrade() -> None:
    op.drop_index("ix_task_threads_attempt_count", table_name="task_threads")
    op.drop_column("task_threads", "attempt_count")