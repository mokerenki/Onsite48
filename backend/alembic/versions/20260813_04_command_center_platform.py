"""Add command-center, source-library, browser-bridge, notification, and trigger records.

Revision ID: 20260813_04
Revises: 20260812_03
Create Date: 2026-08-13
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260813_04"
down_revision = "20260812_03"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)
JSON = sa.JSON()


def upgrade() -> None:
    op.create_table(
        "browser_bridge_devices",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("organization_id", UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("device_id", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("pairing_secret_hash", sa.String(length=255), nullable=True),
        sa.Column("access_token_hash", sa.String(length=255), nullable=True),
        sa.Column("granted_capabilities", JSON, nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "device_id", name="uq_bridge_device_per_org"),
    )
    op.create_index("ix_bridge_devices_organization", "browser_bridge_devices", ["organization_id"])
    op.create_index("ix_bridge_devices_user", "browser_bridge_devices", ["user_id"])
    op.create_index("ix_bridge_devices_status", "browser_bridge_devices", ["status"])

    op.create_table(
        "browser_bridge_commands",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("device_id", UUID, sa.ForeignKey("browser_bridge_devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", UUID, sa.ForeignKey("task_threads.id", ondelete="SET NULL"), nullable=True),
        sa.Column("command_type", sa.String(length=64), nullable=False),
        sa.Column("payload", JSON, nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("requires_approval", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("result", JSON, nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("device_id", "organization_id", "user_id", "task_id", "command_type", "status"):
        op.create_index(f"ix_bridge_commands_{column}", "browser_bridge_commands", [column])

    op.create_table(
        "library_collections",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("organization_id", UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by_user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "name", name="uq_library_collection_name"),
    )
    op.create_index("ix_library_collections_organization", "library_collections", ["organization_id"])

    op.create_table(
        "library_documents",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("organization_id", UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("collection_id", UUID, sa.ForeignKey("library_collections.id", ondelete="SET NULL"), nullable=True),
        sa.Column("uploaded_by_user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_attachment_id", sa.String(length=64), nullable=True),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False, server_default="upload"),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="ready"),
        sa.Column("content_text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("metadata_json", JSON, nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("organization_id", "collection_id", "uploaded_by_user_id", "source_attachment_id", "status", "content_hash"):
        op.create_index(f"ix_library_documents_{column}", "library_documents", [column])

    op.create_table(
        "library_chunks",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("document_id", UUID, sa.ForeignKey("library_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_library_chunk_index"),
    )
    op.create_index("ix_library_chunks_document", "library_chunks", ["document_id"])
    op.create_index("ix_library_chunks_organization", "library_chunks", ["organization_id"])

    op.create_table(
        "command_projects",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("organization_id", UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by_user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_command_projects_organization", "command_projects", ["organization_id"])
    op.create_index("ix_command_projects_status", "command_projects", ["status"])

    op.create_table(
        "command_work_items",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("project_id", UUID, sa.ForeignKey("command_projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_by_user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("assignee_user_id", UUID, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("task_id", UUID, sa.ForeignKey("task_threads.id", ondelete="SET NULL"), nullable=True),
        sa.Column("proposal_id", UUID, sa.ForeignKey("action_proposals.id", ondelete="SET NULL"), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="todo"),
        sa.Column("priority", sa.String(length=16), nullable=False, server_default="medium"),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("project_id", "organization_id", "created_by_user_id", "assignee_user_id", "task_id", "proposal_id", "status"):
        op.create_index(f"ix_command_work_items_{column}", "command_work_items", [column])

    op.create_table(
        "work_item_comments",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("work_item_id", UUID, sa.ForeignKey("command_work_items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("author_user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_work_item_comments_work_item", "work_item_comments", ["work_item_id"])

    op.create_table(
        "notifications",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("organization_id", UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("notification_type", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("href", sa.String(length=512), nullable=True),
        sa.Column("metadata_json", JSON, nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("organization_id", "user_id", "notification_type", "created_at"):
        op.create_index(f"ix_notifications_{column}", "notifications", [column])

    op.create_table(
        "automation_triggers",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("organization_id", UUID, sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("trigger_type", sa.String(length=32), nullable=False, server_default="webhook"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("secret_hash", sa.String(length=255), nullable=False),
        sa.Column("configuration", JSON, nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("last_received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "name", name="uq_automation_trigger_name"),
    )
    for column in ("organization_id", "user_id", "trigger_type"):
        op.create_index(f"ix_automation_triggers_{column}", "automation_triggers", [column])


def downgrade() -> None:
    for table in (
        "automation_triggers", "notifications", "work_item_comments", "command_work_items", "command_projects",
        "library_chunks", "library_documents", "library_collections", "browser_bridge_commands", "browser_bridge_devices",
    ):
        op.drop_table(table)