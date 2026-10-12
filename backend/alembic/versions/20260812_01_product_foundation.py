"""Create Onsite48 product foundation.

Revision ID: 20260812_01
Revises:
Create Date: 2026-08-12
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260812_01"
down_revision = None
branch_labels = None
depends_on = None

# PostgreSQL named enums are created explicitly with checkfirst=True below.
# create_type=False prevents a table definition from issuing a second,
# unconditional CREATE TYPE against a legacy database where the enum exists.
USER_ROLE = postgresql.ENUM("owner", "admin", "member", name="user_role", create_type=False)
TASK_STATUS = postgresql.ENUM("queued", "running", "paused", "waiting_for_approval", "completed", "failed", "cancelled", name="task_status", create_type=False)
CONNECTOR_AUTH_TYPE = postgresql.ENUM("oauth2", "api_key", name="connector_auth_type", create_type=False)
CONNECTOR_STATUS = postgresql.ENUM("disconnected", "connected", "error", "expired", name="connector_status", create_type=False)


def _is_offline() -> bool:
    return bool(op.get_context().as_sql)


def _has_table(table_name: str) -> bool:
    if _is_offline():
        return False
    return sa.inspect(op.get_bind()).has_table(table_name)


def _has_index(table_name: str, index_name: str) -> bool:
    if _is_offline() or not _has_table(table_name):
        return False
    return index_name in {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table_name)}


def _create_table_if_missing(table_name: str, *columns: object, **kwargs: object) -> None:
    if not _has_table(table_name):
        op.create_table(table_name, *columns, **kwargs)


def _create_index_if_missing(index_name: str, table_name: str, columns: list[str], **kwargs: object) -> None:
    if not _has_index(table_name, index_name):
        op.create_index(index_name, table_name, columns, **kwargs)


def upgrade() -> None:
    # Historical revisions must define their own schema. Importing Base.metadata
    # here previously pre-created every future feature table and caused later
    # migrations to fail when they added columns that already existed.
    bind = op.get_bind()
    for enum_type in (USER_ROLE, TASK_STATUS, CONNECTOR_AUTH_TYPE, CONNECTOR_STATUS):
        enum_type.create(bind, checkfirst=True)

    _create_table_if_missing(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False, unique=True),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("avatar_url", sa.String(length=2048), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_email_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    _create_index_if_missing("ix_users_email", "users", ["email"], unique=True)

    _create_table_if_missing(
        "organizations",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("slug", sa.String(length=160), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    _create_index_if_missing("ix_organizations_slug", "organizations", ["slug"], unique=True)

    _create_table_if_missing(
        "organization_members",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", USER_ROLE, nullable=False, server_default="member"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "user_id", name="uq_organization_member"),
    )
    _create_index_if_missing("ix_organization_members_organization_id", "organization_members", ["organization_id"])
    _create_index_if_missing("ix_organization_members_user_id", "organization_members", ["user_id"])

    _create_table_if_missing(
        "user_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("refresh_token_hash", sa.String(length=255), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    _create_index_if_missing("ix_user_sessions_user_id", "user_sessions", ["user_id"])

    _create_table_if_missing(
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
    _create_index_if_missing("ix_oauth_accounts_user_id", "oauth_accounts", ["user_id"])

    _create_table_if_missing(
        "task_threads",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("external_thread_id", sa.String(length=128), nullable=False, unique=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("domain", sa.String(length=64), nullable=False, server_default="general"),
        sa.Column("status", TASK_STATUS, nullable=False, server_default="queued"),
        sa.Column("final_answer", sa.Text(), nullable=True),
        sa.Column("cost_metrics", sa.JSON(), nullable=False),
        sa.Column("execution_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    _create_index_if_missing("ix_task_threads_external_thread_id", "task_threads", ["external_thread_id"], unique=True)
    _create_index_if_missing("ix_task_threads_user_id", "task_threads", ["user_id"])
    _create_index_if_missing("ix_task_threads_organization_id", "task_threads", ["organization_id"])
    _create_index_if_missing("ix_task_threads_status", "task_threads", ["status"])

    _create_table_if_missing(
        "connector_connections",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("connector_id", sa.String(length=100), nullable=False),
        sa.Column("auth_type", CONNECTOR_AUTH_TYPE, nullable=False),
        sa.Column("status", CONNECTOR_STATUS, nullable=False, server_default="disconnected"),
        sa.Column("encrypted_credentials", sa.Text(), nullable=True),
        sa.Column("external_account_id", sa.String(length=255), nullable=True),
        sa.Column("external_account_name", sa.String(length=255), nullable=True),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("organization_id", "user_id", "connector_id", name="uq_connector_connection"),
    )
    _create_index_if_missing("ix_connector_connections_organization_id", "connector_connections", ["organization_id"])
    _create_index_if_missing("ix_connector_connections_user_id", "connector_connections", ["user_id"])
    _create_index_if_missing("ix_connector_connections_connector_id", "connector_connections", ["connector_id"])

    _create_table_if_missing(
        "oauth_authorization_states",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("state", sa.String(length=255), nullable=False, unique=True),
        sa.Column("connector_id", sa.String(length=100), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code_verifier", sa.String(length=255), nullable=True),
        sa.Column("redirect_uri", sa.String(length=2048), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    _create_index_if_missing("ix_oauth_authorization_states_state", "oauth_authorization_states", ["state"], unique=True)
    _create_index_if_missing("ix_oauth_authorization_states_user_id", "oauth_authorization_states", ["user_id"])
    _create_index_if_missing("ix_oauth_authorization_states_organization_id", "oauth_authorization_states", ["organization_id"])

    _create_table_if_missing(
        "task_events",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("task_threads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("task_id", "sequence", name="uq_task_event_sequence"),
    )
    _create_index_if_missing("ix_task_events_task_id", "task_events", ["task_id"])

    _create_table_if_missing(
        "tool_approvals",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("task_id", sa.Uuid(), sa.ForeignKey("task_threads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tool_name", sa.String(length=128), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("arguments", sa.JSON(), nullable=False),
        sa.Column("decision", sa.Boolean(), nullable=True),
        sa.Column("decided_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    _create_index_if_missing("ix_tool_approvals_task_id", "tool_approvals", ["task_id"])


def downgrade() -> None:
    # Later revisions remove their tables before this point. Drop dependent core
    # tables in reverse foreign-key order, then the enum types they owned.
    for table_name in (
        "tool_approvals", "task_events", "oauth_authorization_states",
        "connector_connections", "task_threads", "oauth_accounts",
        "user_sessions", "organization_members", "organizations", "users",
    ):
        op.drop_table(table_name)
    if not _is_offline():
        bind = op.get_bind()
        for enum_type in (CONNECTOR_STATUS, CONNECTOR_AUTH_TYPE, TASK_STATUS, USER_ROLE):
            enum_type.drop(bind, checkfirst=True)