BEGIN;

CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL, 
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

-- Running upgrade  -> 20260812_01

CREATE TYPE user_role AS ENUM ('OWNER', 'ADMIN', 'MEMBER');

CREATE TYPE task_status AS ENUM ('QUEUED', 'RUNNING', 'PAUSED', 'WAITING_FOR_APPROVAL', 'COMPLETED', 'FAILED', 'CANCELLED');

CREATE TYPE connector_auth_type AS ENUM ('OAUTH2', 'API_KEY');

CREATE TYPE connector_status AS ENUM ('DISCONNECTED', 'CONNECTED', 'ERROR', 'EXPIRED');

CREATE TYPE proactive_signal_status AS ENUM ('OPEN', 'DISMISSED', 'RESOLVED');

CREATE TYPE proposal_status AS ENUM ('PENDING', 'APPROVED', 'REJECTED', 'EXECUTED', 'FAILED');

CREATE TABLE users (
    id UUID NOT NULL, 
    email VARCHAR(320) NOT NULL, 
    display_name VARCHAR(120) NOT NULL, 
    password_hash VARCHAR(255), 
    avatar_url VARCHAR(2048), 
    is_active BOOLEAN NOT NULL, 
    is_email_verified BOOLEAN NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id)
);

CREATE UNIQUE INDEX ix_users_email ON users (email);

CREATE TABLE organizations (
    id UUID NOT NULL, 
    name VARCHAR(160) NOT NULL, 
    slug VARCHAR(160) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id)
);

CREATE UNIQUE INDEX ix_organizations_slug ON organizations (slug);

CREATE TABLE organization_members (
    id UUID NOT NULL, 
    organization_id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    role user_role NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_organization_member UNIQUE (organization_id, user_id), 
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE, 
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_organization_members_organization_id ON organization_members (organization_id);

CREATE INDEX ix_organization_members_user_id ON organization_members (user_id);

CREATE TABLE user_sessions (
    id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    refresh_token_hash VARCHAR(255) NOT NULL, 
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    revoked_at TIMESTAMP WITH TIME ZONE, 
    user_agent VARCHAR(512), 
    ip_address VARCHAR(64), 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
    UNIQUE (refresh_token_hash)
);

CREATE INDEX ix_user_sessions_user_id ON user_sessions (user_id);

CREATE TABLE oauth_accounts (
    id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    provider VARCHAR(64) NOT NULL, 
    provider_account_id VARCHAR(255) NOT NULL, 
    encrypted_access_token TEXT, 
    encrypted_refresh_token TEXT, 
    token_expires_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_oauth_provider_account UNIQUE (provider, provider_account_id), 
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_oauth_accounts_user_id ON oauth_accounts (user_id);

CREATE TABLE task_threads (
    id UUID NOT NULL, 
    external_thread_id VARCHAR(128) NOT NULL, 
    user_id UUID NOT NULL, 
    organization_id UUID NOT NULL, 
    title VARCHAR(240) NOT NULL, 
    prompt TEXT NOT NULL, 
    domain VARCHAR(64) NOT NULL, 
    status task_status NOT NULL, 
    final_answer TEXT, 
    cost_metrics JSON NOT NULL, 
    execution_metadata JSON NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    started_at TIMESTAMP WITH TIME ZONE, 
    completed_at TIMESTAMP WITH TIME ZONE, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_task_threads_organization_id ON task_threads (organization_id);

CREATE INDEX ix_task_threads_status ON task_threads (status);

CREATE INDEX ix_task_threads_user_id ON task_threads (user_id);

CREATE UNIQUE INDEX ix_task_threads_external_thread_id ON task_threads (external_thread_id);

CREATE TABLE connector_connections (
    id UUID NOT NULL, 
    organization_id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    connector_id VARCHAR(100) NOT NULL, 
    auth_type connector_auth_type NOT NULL, 
    status connector_status NOT NULL, 
    encrypted_credentials TEXT, 
    external_account_id VARCHAR(255), 
    external_account_name VARCHAR(255), 
    scopes JSON NOT NULL, 
    expires_at TIMESTAMP WITH TIME ZONE, 
    last_error TEXT, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_connector_connection UNIQUE (organization_id, user_id, connector_id), 
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE, 
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_connector_connections_organization_id ON connector_connections (organization_id);

CREATE INDEX ix_connector_connections_connector_id ON connector_connections (connector_id);

CREATE INDEX ix_connector_connections_user_id ON connector_connections (user_id);

CREATE TABLE oauth_authorization_states (
    id UUID NOT NULL, 
    state VARCHAR(255) NOT NULL, 
    connector_id VARCHAR(100) NOT NULL, 
    user_id UUID NOT NULL, 
    organization_id UUID NOT NULL, 
    code_verifier VARCHAR(255), 
    redirect_uri VARCHAR(2048) NOT NULL, 
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX ix_oauth_authorization_states_state ON oauth_authorization_states (state);

CREATE INDEX ix_oauth_authorization_states_user_id ON oauth_authorization_states (user_id);

CREATE INDEX ix_oauth_authorization_states_organization_id ON oauth_authorization_states (organization_id);

CREATE TABLE automation_subscriptions (
    id UUID NOT NULL, 
    organization_id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    kind VARCHAR(80) NOT NULL, 
    enabled BOOLEAN NOT NULL, 
    timezone VARCHAR(64) NOT NULL, 
    schedule JSON NOT NULL, 
    configuration JSON NOT NULL, 
    last_run_at TIMESTAMP WITH TIME ZONE, 
    next_run_at TIMESTAMP WITH TIME ZONE, 
    failure_count INTEGER NOT NULL, 
    last_error TEXT, 
    last_error_at TIMESTAMP WITH TIME ZONE, 
    dead_lettered_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_automation_subscription_kind UNIQUE (organization_id, user_id, kind), 
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE, 
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_automation_subscriptions_organization_id ON automation_subscriptions (organization_id);

CREATE INDEX ix_automation_subscriptions_user_id ON automation_subscriptions (user_id);

CREATE TABLE scheduled_agent_tasks (
    id UUID NOT NULL, 
    organization_id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    title VARCHAR(240) NOT NULL, 
    prompt TEXT NOT NULL, 
    schedule_type VARCHAR(24) NOT NULL, 
    timezone VARCHAR(64) NOT NULL, 
    run_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    enabled BOOLEAN NOT NULL, 
    last_run_at TIMESTAMP WITH TIME ZONE, 
    next_run_at TIMESTAMP WITH TIME ZONE, 
    last_task_thread_id VARCHAR(128), 
    failure_count INTEGER NOT NULL, 
    last_error TEXT, 
    autonomy_policy JSON NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE, 
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_scheduled_agent_tasks_last_task_thread_id ON scheduled_agent_tasks (last_task_thread_id);

CREATE INDEX ix_scheduled_agent_tasks_enabled ON scheduled_agent_tasks (enabled);

CREATE INDEX ix_scheduled_agent_tasks_next_run_at ON scheduled_agent_tasks (next_run_at);

CREATE INDEX ix_scheduled_agent_tasks_run_at ON scheduled_agent_tasks (run_at);

CREATE INDEX ix_scheduled_agent_tasks_organization_id ON scheduled_agent_tasks (organization_id);

CREATE INDEX ix_scheduled_agent_tasks_user_id ON scheduled_agent_tasks (user_id);

CREATE TABLE proactive_signals (
    id UUID NOT NULL, 
    organization_id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    source VARCHAR(100) NOT NULL, 
    signal_type VARCHAR(100) NOT NULL, 
    title VARCHAR(300) NOT NULL, 
    summary TEXT NOT NULL, 
    severity VARCHAR(24) NOT NULL, 
    confidence INTEGER NOT NULL, 
    deduplication_key VARCHAR(255) NOT NULL, 
    evidence JSON NOT NULL, 
    status proactive_signal_status NOT NULL, 
    detected_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    resolved_at TIMESTAMP WITH TIME ZONE, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_proactive_signal_deduplication UNIQUE (organization_id, deduplication_key), 
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE, 
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_proactive_signals_detected_at ON proactive_signals (detected_at);

CREATE INDEX ix_proactive_signals_severity ON proactive_signals (severity);

CREATE INDEX ix_proactive_signals_user_id ON proactive_signals (user_id);

CREATE INDEX ix_proactive_signals_status ON proactive_signals (status);

CREATE INDEX ix_proactive_signals_organization_id ON proactive_signals (organization_id);

CREATE TABLE browser_bridge_devices (
    id UUID NOT NULL, 
    organization_id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    device_id VARCHAR(128) NOT NULL, 
    display_name VARCHAR(160) NOT NULL, 
    status VARCHAR(24) NOT NULL, 
    pairing_secret_hash VARCHAR(255), 
    access_token_hash VARCHAR(255), 
    granted_capabilities JSON NOT NULL, 
    is_preferred BOOLEAN NOT NULL, 
    active_context JSON NOT NULL, 
    last_seen_at TIMESTAMP WITH TIME ZONE, 
    paired_at TIMESTAMP WITH TIME ZONE, 
    revoked_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_bridge_device_per_org UNIQUE (organization_id, device_id), 
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE, 
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_browser_bridge_devices_user_id ON browser_bridge_devices (user_id);

CREATE INDEX ix_browser_bridge_devices_is_preferred ON browser_bridge_devices (is_preferred);

CREATE INDEX ix_browser_bridge_devices_organization_id ON browser_bridge_devices (organization_id);

CREATE INDEX ix_browser_bridge_devices_status ON browser_bridge_devices (status);

CREATE INDEX ix_browser_bridge_devices_device_id ON browser_bridge_devices (device_id);

CREATE TABLE library_collections (
    id UUID NOT NULL, 
    organization_id UUID NOT NULL, 
    created_by_user_id UUID NOT NULL, 
    name VARCHAR(160) NOT NULL, 
    description TEXT, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_library_collection_name UNIQUE (organization_id, name), 
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE, 
    FOREIGN KEY(created_by_user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_library_collections_organization_id ON library_collections (organization_id);

CREATE INDEX ix_library_collections_created_by_user_id ON library_collections (created_by_user_id);

CREATE TABLE command_projects (
    id UUID NOT NULL, 
    organization_id UUID NOT NULL, 
    created_by_user_id UUID NOT NULL, 
    name VARCHAR(200) NOT NULL, 
    description TEXT, 
    status VARCHAR(24) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE, 
    FOREIGN KEY(created_by_user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_command_projects_created_by_user_id ON command_projects (created_by_user_id);

CREATE INDEX ix_command_projects_status ON command_projects (status);

CREATE INDEX ix_command_projects_organization_id ON command_projects (organization_id);

CREATE TABLE notifications (
    id UUID NOT NULL, 
    organization_id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    notification_type VARCHAR(64) NOT NULL, 
    title VARCHAR(300) NOT NULL, 
    body TEXT NOT NULL, 
    href VARCHAR(512), 
    metadata_json JSON NOT NULL, 
    read_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE, 
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_notifications_notification_type ON notifications (notification_type);

CREATE INDEX ix_notifications_organization_id ON notifications (organization_id);

CREATE INDEX ix_notifications_user_id ON notifications (user_id);

CREATE INDEX ix_notifications_created_at ON notifications (created_at);

CREATE TABLE automation_triggers (
    id UUID NOT NULL, 
    organization_id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    name VARCHAR(160) NOT NULL, 
    trigger_type VARCHAR(32) NOT NULL, 
    enabled BOOLEAN NOT NULL, 
    secret_hash VARCHAR(255) NOT NULL, 
    configuration JSON NOT NULL, 
    last_received_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_automation_trigger_name UNIQUE (organization_id, name), 
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE, 
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_automation_triggers_trigger_type ON automation_triggers (trigger_type);

CREATE INDEX ix_automation_triggers_user_id ON automation_triggers (user_id);

CREATE INDEX ix_automation_triggers_organization_id ON automation_triggers (organization_id);

CREATE TABLE morning_briefings (
    id UUID NOT NULL, 
    organization_id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    briefing_date VARCHAR(10) NOT NULL, 
    summary TEXT NOT NULL, 
    signal_count INTEGER NOT NULL, 
    proposal_count INTEGER NOT NULL, 
    metadata_json JSON NOT NULL, 
    delivered_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_morning_briefing_per_day UNIQUE (organization_id, user_id, briefing_date), 
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE, 
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_morning_briefings_user_id ON morning_briefings (user_id);

CREATE INDEX ix_morning_briefings_organization_id ON morning_briefings (organization_id);

CREATE TABLE custom_connectors (
    id UUID NOT NULL, 
    organization_id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    name VARCHAR(160) NOT NULL, 
    slug VARCHAR(80) NOT NULL, 
    kind VARCHAR(24) NOT NULL, 
    base_url VARCHAR(2048) NOT NULL, 
    health_path VARCHAR(1024), 
    description TEXT, 
    usage_guide TEXT, 
    configuration JSON NOT NULL, 
    encrypted_credentials TEXT, 
    status VARCHAR(24) NOT NULL, 
    last_tested_at TIMESTAMP WITH TIME ZONE, 
    last_error TEXT, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_custom_connector_owner_slug UNIQUE (organization_id, user_id, slug), 
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE, 
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_custom_connectors_slug ON custom_connectors (slug);

CREATE INDEX ix_custom_connectors_status ON custom_connectors (status);

CREATE INDEX ix_custom_connectors_kind ON custom_connectors (kind);

CREATE INDEX ix_custom_connectors_user_id ON custom_connectors (user_id);

CREATE INDEX ix_custom_connectors_organization_id ON custom_connectors (organization_id);

CREATE TABLE marketing_campaigns (
    id UUID NOT NULL, 
    organization_id UUID NOT NULL, 
    created_by_user_id UUID NOT NULL, 
    owner_user_id UUID, 
    title VARCHAR(240) NOT NULL, 
    objective TEXT NOT NULL, 
    channel VARCHAR(100), 
    status VARCHAR(32) NOT NULL, 
    due_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE, 
    FOREIGN KEY(created_by_user_id) REFERENCES users (id) ON DELETE CASCADE, 
    FOREIGN KEY(owner_user_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_marketing_campaigns_owner_user_id ON marketing_campaigns (owner_user_id);

CREATE INDEX ix_marketing_campaigns_due_at ON marketing_campaigns (due_at);

CREATE INDEX ix_marketing_campaigns_status ON marketing_campaigns (status);

CREATE INDEX ix_marketing_campaigns_created_by_user_id ON marketing_campaigns (created_by_user_id);

CREATE INDEX ix_marketing_campaigns_organization_id ON marketing_campaigns (organization_id);

CREATE TABLE task_events (
    id UUID NOT NULL, 
    task_id UUID NOT NULL, 
    sequence INTEGER NOT NULL, 
    event_type VARCHAR(100) NOT NULL, 
    payload JSON NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_task_event_sequence UNIQUE (task_id, sequence), 
    FOREIGN KEY(task_id) REFERENCES task_threads (id) ON DELETE CASCADE
);

CREATE INDEX ix_task_events_task_id ON task_events (task_id);

CREATE TABLE tool_approvals (
    id UUID NOT NULL, 
    task_id UUID NOT NULL, 
    tool_name VARCHAR(128) NOT NULL, 
    step_index INTEGER NOT NULL, 
    arguments JSON NOT NULL, 
    decision BOOLEAN, 
    decided_by_user_id UUID, 
    decided_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(task_id) REFERENCES task_threads (id) ON DELETE CASCADE, 
    FOREIGN KEY(decided_by_user_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_tool_approvals_task_id ON tool_approvals (task_id);

CREATE TABLE action_proposals (
    id UUID NOT NULL, 
    organization_id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    signal_id UUID, 
    connector_id VARCHAR(100), 
    action_type VARCHAR(100) NOT NULL, 
    title VARCHAR(300) NOT NULL, 
    rationale TEXT NOT NULL, 
    payload JSON NOT NULL, 
    status proposal_status NOT NULL, 
    approved_by_user_id UUID, 
    decided_at TIMESTAMP WITH TIME ZONE, 
    execution_result JSON NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE, 
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
    FOREIGN KEY(signal_id) REFERENCES proactive_signals (id) ON DELETE SET NULL, 
    FOREIGN KEY(approved_by_user_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_action_proposals_status ON action_proposals (status);

CREATE INDEX ix_action_proposals_signal_id ON action_proposals (signal_id);

CREATE INDEX ix_action_proposals_user_id ON action_proposals (user_id);

CREATE INDEX ix_action_proposals_organization_id ON action_proposals (organization_id);

CREATE TABLE browser_bridge_trust_rules (
    id UUID NOT NULL, 
    device_id UUID NOT NULL, 
    organization_id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    hostname VARCHAR(255) NOT NULL, 
    allowed_capabilities JSON NOT NULL, 
    enabled BOOLEAN NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_bridge_trust_rule_device_host UNIQUE (device_id, hostname), 
    FOREIGN KEY(device_id) REFERENCES browser_bridge_devices (id) ON DELETE CASCADE, 
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE, 
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_browser_bridge_trust_rules_user_id ON browser_bridge_trust_rules (user_id);

CREATE INDEX ix_browser_bridge_trust_rules_organization_id ON browser_bridge_trust_rules (organization_id);

CREATE INDEX ix_browser_bridge_trust_rules_device_id ON browser_bridge_trust_rules (device_id);

CREATE TABLE browser_bridge_commands (
    id UUID NOT NULL, 
    device_id UUID NOT NULL, 
    organization_id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    task_id UUID, 
    command_type VARCHAR(64) NOT NULL, 
    payload JSON NOT NULL, 
    status VARCHAR(24) NOT NULL, 
    preflight_status VARCHAR(32) NOT NULL, 
    preflight_origin VARCHAR(2048), 
    preflight_reason VARCHAR(512), 
    preflight_at TIMESTAMP WITH TIME ZONE, 
    requires_approval BOOLEAN NOT NULL, 
    result JSON NOT NULL, 
    evidence JSON NOT NULL, 
    verification JSON NOT NULL, 
    error TEXT, 
    cancelled_at TIMESTAMP WITH TIME ZONE, 
    cancel_reason TEXT, 
    claimed_at TIMESTAMP WITH TIME ZONE, 
    claim_lease_expires_at TIMESTAMP WITH TIME ZONE, 
    lease_token VARCHAR(128), 
    lease_expires_at TIMESTAMP WITH TIME ZONE, 
    completed_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(device_id) REFERENCES browser_bridge_devices (id) ON DELETE CASCADE, 
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE, 
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
    FOREIGN KEY(task_id) REFERENCES task_threads (id) ON DELETE SET NULL
);

CREATE INDEX ix_browser_bridge_commands_claim_lease_expires_at ON browser_bridge_commands (claim_lease_expires_at);

CREATE INDEX ix_browser_bridge_commands_organization_id ON browser_bridge_commands (organization_id);

CREATE INDEX ix_browser_bridge_commands_status ON browser_bridge_commands (status);

CREATE INDEX ix_browser_bridge_commands_lease_token ON browser_bridge_commands (lease_token);

CREATE INDEX ix_browser_bridge_commands_user_id ON browser_bridge_commands (user_id);

CREATE INDEX ix_browser_bridge_commands_task_id ON browser_bridge_commands (task_id);

CREATE INDEX ix_browser_bridge_commands_device_id ON browser_bridge_commands (device_id);

CREATE INDEX ix_browser_bridge_commands_preflight_status ON browser_bridge_commands (preflight_status);

CREATE INDEX ix_browser_bridge_commands_lease_expires_at ON browser_bridge_commands (lease_expires_at);

CREATE INDEX ix_browser_bridge_commands_command_type ON browser_bridge_commands (command_type);

CREATE TABLE library_documents (
    id UUID NOT NULL, 
    organization_id UUID NOT NULL, 
    collection_id UUID, 
    uploaded_by_user_id UUID NOT NULL, 
    source_attachment_id VARCHAR(64), 
    title VARCHAR(240) NOT NULL, 
    source_type VARCHAR(64) NOT NULL, 
    status VARCHAR(24) NOT NULL, 
    content_text TEXT NOT NULL, 
    content_hash VARCHAR(64) NOT NULL, 
    char_count INTEGER NOT NULL, 
    metadata_json JSON NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE, 
    FOREIGN KEY(collection_id) REFERENCES library_collections (id) ON DELETE SET NULL, 
    FOREIGN KEY(uploaded_by_user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_library_documents_organization_id ON library_documents (organization_id);

CREATE INDEX ix_library_documents_status ON library_documents (status);

CREATE INDEX ix_library_documents_collection_id ON library_documents (collection_id);

CREATE INDEX ix_library_documents_uploaded_by_user_id ON library_documents (uploaded_by_user_id);

CREATE INDEX ix_library_documents_content_hash ON library_documents (content_hash);

CREATE INDEX ix_library_documents_source_attachment_id ON library_documents (source_attachment_id);

CREATE TABLE scheduled_task_executions (
    id UUID NOT NULL, 
    schedule_id UUID NOT NULL, 
    organization_id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    task_thread_id VARCHAR(128), 
    attempt INTEGER NOT NULL, 
    status VARCHAR(32) NOT NULL, 
    scheduled_for TIMESTAMP WITH TIME ZONE NOT NULL, 
    started_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    heartbeat_at TIMESTAMP WITH TIME ZONE, 
    lease_expires_at TIMESTAMP WITH TIME ZONE, 
    completed_at TIMESTAMP WITH TIME ZONE, 
    error TEXT, 
    summary TEXT, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(schedule_id) REFERENCES scheduled_agent_tasks (id) ON DELETE CASCADE, 
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE, 
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_scheduled_task_executions_user_id ON scheduled_task_executions (user_id);

CREATE INDEX ix_scheduled_task_executions_task_thread_id ON scheduled_task_executions (task_thread_id);

CREATE INDEX ix_scheduled_task_executions_lease_expires_at ON scheduled_task_executions (lease_expires_at);

CREATE INDEX ix_scheduled_task_executions_status ON scheduled_task_executions (status);

CREATE INDEX ix_scheduled_task_executions_organization_id ON scheduled_task_executions (organization_id);

CREATE INDEX ix_scheduled_task_executions_schedule_id ON scheduled_task_executions (schedule_id);

CREATE INDEX ix_scheduled_task_executions_scheduled_for ON scheduled_task_executions (scheduled_for);

CREATE TABLE library_chunks (
    id UUID NOT NULL, 
    document_id UUID NOT NULL, 
    organization_id UUID NOT NULL, 
    chunk_index INTEGER NOT NULL, 
    content TEXT NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_library_chunk_index UNIQUE (document_id, chunk_index), 
    FOREIGN KEY(document_id) REFERENCES library_documents (id) ON DELETE CASCADE, 
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_library_chunks_organization_id ON library_chunks (organization_id);

CREATE INDEX ix_library_chunks_document_id ON library_chunks (document_id);

CREATE TABLE command_work_items (
    id UUID NOT NULL, 
    project_id UUID NOT NULL, 
    organization_id UUID NOT NULL, 
    created_by_user_id UUID NOT NULL, 
    assignee_user_id UUID, 
    task_id UUID, 
    proposal_id UUID, 
    title VARCHAR(300) NOT NULL, 
    description TEXT, 
    status VARCHAR(24) NOT NULL, 
    priority VARCHAR(16) NOT NULL, 
    due_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(project_id) REFERENCES command_projects (id) ON DELETE CASCADE, 
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE, 
    FOREIGN KEY(created_by_user_id) REFERENCES users (id) ON DELETE CASCADE, 
    FOREIGN KEY(assignee_user_id) REFERENCES users (id) ON DELETE SET NULL, 
    FOREIGN KEY(task_id) REFERENCES task_threads (id) ON DELETE SET NULL, 
    FOREIGN KEY(proposal_id) REFERENCES action_proposals (id) ON DELETE SET NULL
);

CREATE INDEX ix_command_work_items_organization_id ON command_work_items (organization_id);

CREATE INDEX ix_command_work_items_project_id ON command_work_items (project_id);

CREATE INDEX ix_command_work_items_proposal_id ON command_work_items (proposal_id);

CREATE INDEX ix_command_work_items_task_id ON command_work_items (task_id);

CREATE INDEX ix_command_work_items_status ON command_work_items (status);

CREATE INDEX ix_command_work_items_assignee_user_id ON command_work_items (assignee_user_id);

CREATE INDEX ix_command_work_items_created_by_user_id ON command_work_items (created_by_user_id);

CREATE TABLE work_item_comments (
    id UUID NOT NULL, 
    work_item_id UUID NOT NULL, 
    author_user_id UUID NOT NULL, 
    body TEXT NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(work_item_id) REFERENCES command_work_items (id) ON DELETE CASCADE, 
    FOREIGN KEY(author_user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_work_item_comments_work_item_id ON work_item_comments (work_item_id);

CREATE INDEX ix_work_item_comments_author_user_id ON work_item_comments (author_user_id);

INSERT INTO alembic_version (version_num) VALUES ('20260812_01') RETURNING alembic_version.version_num;

-- Running upgrade 20260812_01 -> 20260812_02

CREATE TYPE user_role AS ENUM ('OWNER', 'ADMIN', 'MEMBER');

CREATE TYPE task_status AS ENUM ('QUEUED', 'RUNNING', 'PAUSED', 'WAITING_FOR_APPROVAL', 'COMPLETED', 'FAILED', 'CANCELLED');

CREATE TYPE connector_auth_type AS ENUM ('OAUTH2', 'API_KEY');

CREATE TYPE connector_status AS ENUM ('DISCONNECTED', 'CONNECTED', 'ERROR', 'EXPIRED');

CREATE TYPE proactive_signal_status AS ENUM ('OPEN', 'DISMISSED', 'RESOLVED');

CREATE TYPE proposal_status AS ENUM ('PENDING', 'APPROVED', 'REJECTED', 'EXECUTED', 'FAILED');

CREATE TABLE automation_subscriptions (
    id UUID NOT NULL, 
    organization_id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    kind VARCHAR(80) NOT NULL, 
    enabled BOOLEAN NOT NULL, 
    timezone VARCHAR(64) NOT NULL, 
    schedule JSON NOT NULL, 
    configuration JSON NOT NULL, 
    last_run_at TIMESTAMP WITH TIME ZONE, 
    next_run_at TIMESTAMP WITH TIME ZONE, 
    failure_count INTEGER NOT NULL, 
    last_error TEXT, 
    last_error_at TIMESTAMP WITH TIME ZONE, 
    dead_lettered_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_automation_subscription_kind UNIQUE (organization_id, user_id, kind), 
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE, 
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_automation_subscriptions_organization_id ON automation_subscriptions (organization_id);

CREATE INDEX ix_automation_subscriptions_user_id ON automation_subscriptions (user_id);

CREATE TABLE proactive_signals (
    id UUID NOT NULL, 
    organization_id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    source VARCHAR(100) NOT NULL, 
    signal_type VARCHAR(100) NOT NULL, 
    title VARCHAR(300) NOT NULL, 
    summary TEXT NOT NULL, 
    severity VARCHAR(24) NOT NULL, 
    confidence INTEGER NOT NULL, 
    deduplication_key VARCHAR(255) NOT NULL, 
    evidence JSON NOT NULL, 
    status proactive_signal_status NOT NULL, 
    detected_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    resolved_at TIMESTAMP WITH TIME ZONE, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_proactive_signal_deduplication UNIQUE (organization_id, deduplication_key), 
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE, 
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_proactive_signals_detected_at ON proactive_signals (detected_at);

CREATE INDEX ix_proactive_signals_severity ON proactive_signals (severity);

CREATE INDEX ix_proactive_signals_user_id ON proactive_signals (user_id);

CREATE INDEX ix_proactive_signals_status ON proactive_signals (status);

CREATE INDEX ix_proactive_signals_organization_id ON proactive_signals (organization_id);

CREATE TABLE morning_briefings (
    id UUID NOT NULL, 
    organization_id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    briefing_date VARCHAR(10) NOT NULL, 
    summary TEXT NOT NULL, 
    signal_count INTEGER NOT NULL, 
    proposal_count INTEGER NOT NULL, 
    metadata_json JSON NOT NULL, 
    delivered_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_morning_briefing_per_day UNIQUE (organization_id, user_id, briefing_date), 
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE, 
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_morning_briefings_user_id ON morning_briefings (user_id);

CREATE INDEX ix_morning_briefings_organization_id ON morning_briefings (organization_id);

CREATE TABLE action_proposals (
    id UUID NOT NULL, 
    organization_id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    signal_id UUID, 
    connector_id VARCHAR(100), 
    action_type VARCHAR(100) NOT NULL, 
    title VARCHAR(300) NOT NULL, 
    rationale TEXT NOT NULL, 
    payload JSON NOT NULL, 
    status proposal_status NOT NULL, 
    approved_by_user_id UUID, 
    decided_at TIMESTAMP WITH TIME ZONE, 
    execution_result JSON NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE, 
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
    FOREIGN KEY(signal_id) REFERENCES proactive_signals (id) ON DELETE SET NULL, 
    FOREIGN KEY(approved_by_user_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_action_proposals_status ON action_proposals (status);

CREATE INDEX ix_action_proposals_signal_id ON action_proposals (signal_id);

CREATE INDEX ix_action_proposals_user_id ON action_proposals (user_id);

CREATE INDEX ix_action_proposals_organization_id ON action_proposals (organization_id);

UPDATE alembic_version SET version_num='20260812_02' WHERE alembic_version.version_num = '20260812_01';

-- Running upgrade 20260812_02 -> 20260812_03

ALTER TABLE automation_subscriptions ADD COLUMN failure_count INTEGER DEFAULT '0' NOT NULL;

ALTER TABLE automation_subscriptions ADD COLUMN last_error TEXT;

ALTER TABLE automation_subscriptions ADD COLUMN last_error_at TIMESTAMP WITH TIME ZONE;

ALTER TABLE automation_subscriptions ADD COLUMN dead_lettered_at TIMESTAMP WITH TIME ZONE;

UPDATE alembic_version SET version_num='20260812_03' WHERE alembic_version.version_num = '20260812_02';

-- Running upgrade 20260812_03 -> 20260813_04

CREATE TABLE browser_bridge_devices (
    id UUID NOT NULL, 
    organization_id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    device_id VARCHAR(128) NOT NULL, 
    display_name VARCHAR(160) NOT NULL, 
    status VARCHAR(24) DEFAULT 'pending' NOT NULL, 
    pairing_secret_hash VARCHAR(255), 
    access_token_hash VARCHAR(255), 
    granted_capabilities JSON DEFAULT '[]'::json NOT NULL, 
    last_seen_at TIMESTAMP WITH TIME ZONE, 
    paired_at TIMESTAMP WITH TIME ZONE, 
    revoked_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_bridge_device_per_org UNIQUE (organization_id, device_id), 
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE, 
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_bridge_devices_organization ON browser_bridge_devices (organization_id);

CREATE INDEX ix_bridge_devices_user ON browser_bridge_devices (user_id);

CREATE INDEX ix_bridge_devices_status ON browser_bridge_devices (status);

CREATE TABLE browser_bridge_commands (
    id UUID NOT NULL, 
    device_id UUID NOT NULL, 
    organization_id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    task_id UUID, 
    command_type VARCHAR(64) NOT NULL, 
    payload JSON DEFAULT '{}'::json NOT NULL, 
    status VARCHAR(24) DEFAULT 'pending' NOT NULL, 
    requires_approval BOOLEAN DEFAULT true NOT NULL, 
    result JSON DEFAULT '{}'::json NOT NULL, 
    error TEXT, 
    claimed_at TIMESTAMP WITH TIME ZONE, 
    completed_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(device_id) REFERENCES browser_bridge_devices (id) ON DELETE CASCADE, 
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE, 
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
    FOREIGN KEY(task_id) REFERENCES task_threads (id) ON DELETE SET NULL
);

CREATE INDEX ix_bridge_commands_device_id ON browser_bridge_commands (device_id);

CREATE INDEX ix_bridge_commands_organization_id ON browser_bridge_commands (organization_id);

CREATE INDEX ix_bridge_commands_user_id ON browser_bridge_commands (user_id);

CREATE INDEX ix_bridge_commands_task_id ON browser_bridge_commands (task_id);

CREATE INDEX ix_bridge_commands_command_type ON browser_bridge_commands (command_type);

CREATE INDEX ix_bridge_commands_status ON browser_bridge_commands (status);

CREATE TABLE library_collections (
    id UUID NOT NULL, 
    organization_id UUID NOT NULL, 
    created_by_user_id UUID NOT NULL, 
    name VARCHAR(160) NOT NULL, 
    description TEXT, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_library_collection_name UNIQUE (organization_id, name), 
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE, 
    FOREIGN KEY(created_by_user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_library_collections_organization ON library_collections (organization_id);

CREATE TABLE library_documents (
    id UUID NOT NULL, 
    organization_id UUID NOT NULL, 
    collection_id UUID, 
    uploaded_by_user_id UUID NOT NULL, 
    source_attachment_id VARCHAR(64), 
    title VARCHAR(240) NOT NULL, 
    source_type VARCHAR(64) DEFAULT 'upload' NOT NULL, 
    status VARCHAR(24) DEFAULT 'ready' NOT NULL, 
    content_text TEXT NOT NULL, 
    content_hash VARCHAR(64) NOT NULL, 
    char_count INTEGER DEFAULT '0' NOT NULL, 
    metadata_json JSON DEFAULT '{}'::json NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE, 
    FOREIGN KEY(collection_id) REFERENCES library_collections (id) ON DELETE SET NULL, 
    FOREIGN KEY(uploaded_by_user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_library_documents_organization_id ON library_documents (organization_id);

CREATE INDEX ix_library_documents_collection_id ON library_documents (collection_id);

CREATE INDEX ix_library_documents_uploaded_by_user_id ON library_documents (uploaded_by_user_id);

CREATE INDEX ix_library_documents_source_attachment_id ON library_documents (source_attachment_id);

CREATE INDEX ix_library_documents_status ON library_documents (status);

CREATE INDEX ix_library_documents_content_hash ON library_documents (content_hash);

CREATE TABLE library_chunks (
    id UUID NOT NULL, 
    document_id UUID NOT NULL, 
    organization_id UUID NOT NULL, 
    chunk_index INTEGER NOT NULL, 
    content TEXT NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_library_chunk_index UNIQUE (document_id, chunk_index), 
    FOREIGN KEY(document_id) REFERENCES library_documents (id) ON DELETE CASCADE, 
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE INDEX ix_library_chunks_document ON library_chunks (document_id);

CREATE INDEX ix_library_chunks_organization ON library_chunks (organization_id);

CREATE TABLE command_projects (
    id UUID NOT NULL, 
    organization_id UUID NOT NULL, 
    created_by_user_id UUID NOT NULL, 
    name VARCHAR(200) NOT NULL, 
    description TEXT, 
    status VARCHAR(24) DEFAULT 'active' NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE, 
    FOREIGN KEY(created_by_user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_command_projects_organization ON command_projects (organization_id);

CREATE INDEX ix_command_projects_status ON command_projects (status);

CREATE TABLE command_work_items (
    id UUID NOT NULL, 
    project_id UUID NOT NULL, 
    organization_id UUID NOT NULL, 
    created_by_user_id UUID NOT NULL, 
    assignee_user_id UUID, 
    task_id UUID, 
    proposal_id UUID, 
    title VARCHAR(300) NOT NULL, 
    description TEXT, 
    status VARCHAR(24) DEFAULT 'todo' NOT NULL, 
    priority VARCHAR(16) DEFAULT 'medium' NOT NULL, 
    due_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(project_id) REFERENCES command_projects (id) ON DELETE CASCADE, 
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE, 
    FOREIGN KEY(created_by_user_id) REFERENCES users (id) ON DELETE CASCADE, 
    FOREIGN KEY(assignee_user_id) REFERENCES users (id) ON DELETE SET NULL, 
    FOREIGN KEY(task_id) REFERENCES task_threads (id) ON DELETE SET NULL, 
    FOREIGN KEY(proposal_id) REFERENCES action_proposals (id) ON DELETE SET NULL
);

CREATE INDEX ix_command_work_items_project_id ON command_work_items (project_id);

CREATE INDEX ix_command_work_items_organization_id ON command_work_items (organization_id);

CREATE INDEX ix_command_work_items_created_by_user_id ON command_work_items (created_by_user_id);

CREATE INDEX ix_command_work_items_assignee_user_id ON command_work_items (assignee_user_id);

CREATE INDEX ix_command_work_items_task_id ON command_work_items (task_id);

CREATE INDEX ix_command_work_items_proposal_id ON command_work_items (proposal_id);

CREATE INDEX ix_command_work_items_status ON command_work_items (status);

CREATE TABLE work_item_comments (
    id UUID NOT NULL, 
    work_item_id UUID NOT NULL, 
    author_user_id UUID NOT NULL, 
    body TEXT NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(work_item_id) REFERENCES command_work_items (id) ON DELETE CASCADE, 
    FOREIGN KEY(author_user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_work_item_comments_work_item ON work_item_comments (work_item_id);

CREATE TABLE notifications (
    id UUID NOT NULL, 
    organization_id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    notification_type VARCHAR(64) NOT NULL, 
    title VARCHAR(300) NOT NULL, 
    body TEXT NOT NULL, 
    href VARCHAR(512), 
    metadata_json JSON DEFAULT '{}'::json NOT NULL, 
    read_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE, 
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_notifications_organization_id ON notifications (organization_id);

CREATE INDEX ix_notifications_user_id ON notifications (user_id);

CREATE INDEX ix_notifications_notification_type ON notifications (notification_type);

CREATE INDEX ix_notifications_created_at ON notifications (created_at);

CREATE TABLE automation_triggers (
    id UUID NOT NULL, 
    organization_id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    name VARCHAR(160) NOT NULL, 
    trigger_type VARCHAR(32) DEFAULT 'webhook' NOT NULL, 
    enabled BOOLEAN DEFAULT true NOT NULL, 
    secret_hash VARCHAR(255) NOT NULL, 
    configuration JSON DEFAULT '{}'::json NOT NULL, 
    last_received_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_automation_trigger_name UNIQUE (organization_id, name), 
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE, 
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_automation_triggers_organization_id ON automation_triggers (organization_id);

CREATE INDEX ix_automation_triggers_user_id ON automation_triggers (user_id);

CREATE INDEX ix_automation_triggers_trigger_type ON automation_triggers (trigger_type);

UPDATE alembic_version SET version_num='20260813_04' WHERE alembic_version.version_num = '20260812_03';

-- Running upgrade 20260813_04 -> 20260815_05

ALTER TABLE browser_bridge_devices ADD COLUMN active_context JSON DEFAULT '{}'::json NOT NULL;

CREATE TABLE browser_bridge_trust_rules (
    id UUID NOT NULL, 
    device_id UUID NOT NULL, 
    organization_id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    hostname VARCHAR(255) NOT NULL, 
    allowed_capabilities JSON DEFAULT '[]'::json NOT NULL, 
    enabled BOOLEAN DEFAULT true NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_bridge_trust_rule_device_host UNIQUE (device_id, hostname), 
    FOREIGN KEY(device_id) REFERENCES browser_bridge_devices (id) ON DELETE CASCADE, 
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE, 
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_bridge_trust_rules_device_id ON browser_bridge_trust_rules (device_id);

CREATE INDEX ix_bridge_trust_rules_organization_id ON browser_bridge_trust_rules (organization_id);

CREATE INDEX ix_bridge_trust_rules_user_id ON browser_bridge_trust_rules (user_id);

CREATE INDEX ix_bridge_trust_rules_hostname ON browser_bridge_trust_rules (hostname);

UPDATE alembic_version SET version_num='20260815_05' WHERE alembic_version.version_num = '20260813_04';

-- Running upgrade 20260815_05 -> 20260817_06

CREATE TABLE scheduled_agent_tasks (
    id UUID NOT NULL, 
    organization_id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    title VARCHAR(240) NOT NULL, 
    prompt TEXT NOT NULL, 
    schedule_type VARCHAR(24) DEFAULT 'once' NOT NULL, 
    timezone VARCHAR(64) DEFAULT 'UTC' NOT NULL, 
    run_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    enabled BOOLEAN DEFAULT true NOT NULL, 
    last_run_at TIMESTAMP WITH TIME ZONE, 
    next_run_at TIMESTAMP WITH TIME ZONE, 
    last_task_thread_id VARCHAR(128), 
    failure_count INTEGER DEFAULT '0' NOT NULL, 
    last_error TEXT, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE, 
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_scheduled_agent_tasks_org ON scheduled_agent_tasks (organization_id);

CREATE INDEX ix_scheduled_agent_tasks_user ON scheduled_agent_tasks (user_id);

CREATE INDEX ix_scheduled_agent_tasks_run_at ON scheduled_agent_tasks (run_at);

CREATE INDEX ix_scheduled_agent_tasks_next_run_at ON scheduled_agent_tasks (next_run_at);

UPDATE alembic_version SET version_num='20260817_06' WHERE alembic_version.version_num = '20260815_05';

-- Running upgrade 20260817_06 -> 20260819_07

ALTER TABLE browser_bridge_commands ADD COLUMN evidence JSON DEFAULT '{}'::json NOT NULL;

ALTER TABLE browser_bridge_commands ADD COLUMN verification JSON DEFAULT '{}'::json NOT NULL;

ALTER TABLE browser_bridge_commands ADD COLUMN cancelled_at TIMESTAMP WITH TIME ZONE;

ALTER TABLE browser_bridge_commands ADD COLUMN cancel_reason TEXT;

UPDATE alembic_version SET version_num='20260819_07' WHERE alembic_version.version_num = '20260817_06';

-- Running upgrade 20260819_07 -> 20260819_08

CREATE TABLE custom_connectors (
    id UUID NOT NULL, 
    organization_id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    name VARCHAR(160) NOT NULL, 
    slug VARCHAR(80) NOT NULL, 
    kind VARCHAR(24) NOT NULL, 
    base_url VARCHAR(2048) NOT NULL, 
    health_path VARCHAR(1024), 
    description TEXT, 
    usage_guide TEXT, 
    configuration JSONB DEFAULT '{}'::jsonb NOT NULL, 
    encrypted_credentials TEXT, 
    status VARCHAR(24) DEFAULT 'configured' NOT NULL, 
    last_tested_at TIMESTAMP WITH TIME ZONE, 
    last_error TEXT, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    CONSTRAINT uq_custom_connector_owner_slug UNIQUE (organization_id, user_id, slug), 
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE, 
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_custom_connectors_organization_id ON custom_connectors (organization_id);

CREATE INDEX ix_custom_connectors_user_id ON custom_connectors (user_id);

CREATE INDEX ix_custom_connectors_slug ON custom_connectors (slug);

CREATE INDEX ix_custom_connectors_kind ON custom_connectors (kind);

CREATE INDEX ix_custom_connectors_status ON custom_connectors (status);

CREATE TABLE scheduled_task_executions (
    id UUID NOT NULL, 
    schedule_id UUID NOT NULL, 
    organization_id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    task_thread_id VARCHAR(128), 
    attempt INTEGER DEFAULT '1' NOT NULL, 
    status VARCHAR(32) DEFAULT 'running' NOT NULL, 
    started_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    completed_at TIMESTAMP WITH TIME ZONE, 
    error TEXT, 
    summary TEXT, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(schedule_id) REFERENCES scheduled_agent_tasks (id) ON DELETE CASCADE, 
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE, 
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX ix_scheduled_task_executions_schedule_id ON scheduled_task_executions (schedule_id);

CREATE INDEX ix_scheduled_task_executions_organization_id ON scheduled_task_executions (organization_id);

CREATE INDEX ix_scheduled_task_executions_user_id ON scheduled_task_executions (user_id);

CREATE INDEX ix_scheduled_task_executions_task_thread_id ON scheduled_task_executions (task_thread_id);

CREATE INDEX ix_scheduled_task_executions_status ON scheduled_task_executions (status);

UPDATE alembic_version SET version_num='20260819_08' WHERE alembic_version.version_num = '20260819_07';

-- Running upgrade 20260819_08 -> 20260819_09

CREATE TABLE marketing_campaigns (
    id UUID NOT NULL, 
    organization_id UUID NOT NULL, 
    created_by_user_id UUID NOT NULL, 
    owner_user_id UUID, 
    title VARCHAR(240) NOT NULL, 
    objective TEXT NOT NULL, 
    channel VARCHAR(100), 
    status VARCHAR(32) DEFAULT 'backlog' NOT NULL, 
    due_at TIMESTAMP WITH TIME ZONE, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE, 
    FOREIGN KEY(created_by_user_id) REFERENCES users (id) ON DELETE CASCADE, 
    FOREIGN KEY(owner_user_id) REFERENCES users (id) ON DELETE SET NULL
);

CREATE INDEX ix_marketing_campaigns_organization_id ON marketing_campaigns (organization_id);

CREATE INDEX ix_marketing_campaigns_created_by_user_id ON marketing_campaigns (created_by_user_id);

CREATE INDEX ix_marketing_campaigns_owner_user_id ON marketing_campaigns (owner_user_id);

CREATE INDEX ix_marketing_campaigns_status ON marketing_campaigns (status);

CREATE INDEX ix_marketing_campaigns_due_at ON marketing_campaigns (due_at);

UPDATE alembic_version SET version_num='20260819_09' WHERE alembic_version.version_num = '20260819_08';

-- Running upgrade 20260819_09 -> 20260819_10

ALTER TABLE scheduled_task_executions ADD COLUMN scheduled_for TIMESTAMP WITH TIME ZONE;

ALTER TABLE scheduled_task_executions ADD COLUMN heartbeat_at TIMESTAMP WITH TIME ZONE;

ALTER TABLE scheduled_task_executions ADD COLUMN lease_expires_at TIMESTAMP WITH TIME ZONE;

UPDATE scheduled_task_executions SET scheduled_for = started_at WHERE scheduled_for IS NULL;

ALTER TABLE scheduled_task_executions ALTER COLUMN scheduled_for SET NOT NULL;

CREATE INDEX ix_scheduled_task_executions_scheduled_for ON scheduled_task_executions (scheduled_for);

CREATE INDEX ix_scheduled_task_executions_lease_expires_at ON scheduled_task_executions (lease_expires_at);

UPDATE alembic_version SET version_num='20260819_10' WHERE alembic_version.version_num = '20260819_09';

-- Running upgrade 20260819_10 -> 20260819_11

ALTER TABLE browser_bridge_devices ADD COLUMN is_preferred BOOLEAN DEFAULT false NOT NULL;

CREATE INDEX ix_browser_bridge_devices_is_preferred ON browser_bridge_devices (is_preferred);

ALTER TABLE browser_bridge_commands ADD COLUMN preflight_status VARCHAR(32) DEFAULT 'waiting' NOT NULL;

ALTER TABLE browser_bridge_commands ADD COLUMN preflight_origin VARCHAR(2048);

ALTER TABLE browser_bridge_commands ADD COLUMN preflight_reason VARCHAR(512);

ALTER TABLE browser_bridge_commands ADD COLUMN preflight_at TIMESTAMP WITH TIME ZONE;

ALTER TABLE browser_bridge_commands ADD COLUMN claim_lease_expires_at TIMESTAMP WITH TIME ZONE;

CREATE INDEX ix_browser_bridge_commands_preflight_status ON browser_bridge_commands (preflight_status);

CREATE INDEX ix_browser_bridge_commands_claim_lease_expires_at ON browser_bridge_commands (claim_lease_expires_at);

UPDATE alembic_version SET version_num='20260819_11' WHERE alembic_version.version_num = '20260819_10';

-- Running upgrade 20260819_11 -> 20260821_12

ALTER TABLE browser_bridge_commands ADD COLUMN lease_token VARCHAR(128);

ALTER TABLE browser_bridge_commands ADD COLUMN lease_expires_at TIMESTAMP WITH TIME ZONE;

CREATE INDEX ix_browser_bridge_commands_lease_token ON browser_bridge_commands (lease_token);

CREATE INDEX ix_browser_bridge_commands_lease_expires_at ON browser_bridge_commands (lease_expires_at);

UPDATE alembic_version SET version_num='20260821_12' WHERE alembic_version.version_num = '20260819_11';

-- Running upgrade 20260821_12 -> 20260825_13

ALTER TABLE scheduled_agent_tasks ADD COLUMN autonomy_policy JSON DEFAULT '{"mode":"approval_required","additional_allowed_tools":[],"allowed_custom_mcp_tools":[]}' NOT NULL;

ALTER TABLE scheduled_agent_tasks ALTER COLUMN autonomy_policy DROP DEFAULT;

UPDATE alembic_version SET version_num='20260825_13' WHERE alembic_version.version_num = '20260821_12';

COMMIT;
