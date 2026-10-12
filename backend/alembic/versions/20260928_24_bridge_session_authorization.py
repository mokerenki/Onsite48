"""Close the gap between the browser bridge models and the migrated schema.

Two defects, the same root cause: features were added to the ORM and to the
service layer without a migration, and no test ever touched the affected rows so
nothing loaded the model against a real table.

1. ``BrowserBridgeDevice`` declares ``authorized_origins``,
   ``authorized_capabilities`` and ``session_authorized_at``, read and written
   when a user authorizes a browser session for a task
   (``app/api/browser_bridge.py``). Missing, so bridge status reporting and
   authorization recording both raised ``UndefinedColumnError``.

2. ``TaskContext`` and ``OperationEvent`` were never created as tables at all,
   though ``services/browser_bridge.py`` queries ``TaskContext`` and
   ``api/browser_bridge.py`` inserts ``OperationEvent``. Both raised
   ``UndefinedTableError``.

The grant columns are added NOT NULL with an empty-grant default, which
preserves the fail-closed meaning of Phase 0: a device with no grant rows has no
authorized origins, so an action is refused rather than permitted.

``tests/test_model_schema_parity.py`` now compares every mapped table and
column against the schema Alembic builds from zero, so this cannot recur
unnoticed.

Revision ID: 20260928_24
Revises: 20260927_23
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260928_24"
down_revision = "20260927_23"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_contexts",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "device_id",
            sa.UUID(),
            sa.ForeignKey("browser_bridge_devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            sa.UUID(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "thread_id",
            sa.UUID(),
            sa.ForeignKey("task_threads.id", ondelete="SET NULL"),
        ),
        sa.Column("task_goal", sa.Text()),
        sa.Column("planned_steps", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("completed_steps", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("current_step", sa.String(256)),
        sa.Column("tab_history", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("active_tab_id", sa.Integer()),
        sa.Column("locked_tab_id", sa.Integer()),
        sa.Column("session_start_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_task_contexts_device_id", "task_contexts", ["device_id"])
    op.create_index("ix_task_contexts_organization_id", "task_contexts", ["organization_id"])
    op.create_index("ix_task_contexts_user_id", "task_contexts", ["user_id"])
    op.create_index("ix_task_contexts_thread_id", "task_contexts", ["thread_id"])

    op.create_table(
        "operation_events",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "device_id",
            sa.UUID(),
            sa.ForeignKey("browser_bridge_devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            sa.UUID(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "thread_id",
            sa.UUID(),
            sa.ForeignKey("task_threads.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "session_id",
            sa.UUID(),
            sa.ForeignKey("browser_sessions.id", ondelete="SET NULL"),
        ),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("event_data", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_operation_events_device_id", "operation_events", ["device_id"])
    op.create_index("ix_operation_events_organization_id", "operation_events", ["organization_id"])
    op.create_index("ix_operation_events_user_id", "operation_events", ["user_id"])
    op.create_index("ix_operation_events_thread_id", "operation_events", ["thread_id"])

    op.add_column(
        "browser_bridge_devices",
        sa.Column(
            "authorized_origins",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
    )
    op.add_column(
        "browser_bridge_devices",
        sa.Column(
            "authorized_capabilities",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
    )
    op.add_column(
        "browser_bridge_devices",
        sa.Column("session_authorized_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("browser_bridge_devices", "session_authorized_at")
    op.drop_column("browser_bridge_devices", "authorized_capabilities")
    op.drop_column("browser_bridge_devices", "authorized_origins")

    op.drop_index("ix_operation_events_thread_id", table_name="operation_events")
    op.drop_index("ix_operation_events_user_id", table_name="operation_events")
    op.drop_index("ix_operation_events_organization_id", table_name="operation_events")
    op.drop_index("ix_operation_events_device_id", table_name="operation_events")
    op.drop_table("operation_events")

    op.drop_index("ix_task_contexts_thread_id", table_name="task_contexts")
    op.drop_index("ix_task_contexts_user_id", table_name="task_contexts")
    op.drop_index("ix_task_contexts_organization_id", table_name="task_contexts")
    op.drop_index("ix_task_contexts_device_id", table_name="task_contexts")
    op.drop_table("task_contexts")