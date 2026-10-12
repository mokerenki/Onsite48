BEGIN;

CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL, 
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

-- Running upgrade  -> 20260812_01

CREATE TYPE user_role AS ENUM ('owner', 'admin', 'member');

CREATE TYPE task_status AS ENUM ('queued', 'running', 'paused', 'waiting_for_approval', 'completed', 'failed', 'cancelled');

CREATE TYPE connector_auth_type AS ENUM ('oauth2', 'api_key');

CREATE TYPE connector_status AS ENUM ('disconnected', 'connected', 'error', 'expired');

CREATE TABLE users (
    id UUID NOT NULL, 
    email VARCHAR(320) NOT NULL, 
    display_name VARCHAR(120) NOT NULL, 
    password_hash VARCHAR(255), 
    avatar_url VARCHAR(2048), 
    is_active BOOLEAN DEFAULT true NOT NULL, 
    is_email_verified BOOLEAN DEFAULT false NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    UNIQUE (email)
);

CREATE UNIQUE INDEX ix_users_email ON users (email);

CREATE TABLE organizations (
    id UUID NOT NULL, 
    name VARCHAR(160) NOT NULL, 
    slug VARCHAR(160) NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    UNIQUE (slug)
);

CREATE UNIQUE INDEX ix_organizations_slug ON organizations (slug);

CREATE TABLE organization_members (
    id UUID NOT NULL, 
    organization_id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    role user_role DEFAULT 'member' NOT NULL, 
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
    domain VARCHAR(64) DEFAULT 'general' NOT NULL, 
    status task_status DEFAULT 'queued' NOT NULL, 
    final_answer TEXT, 
    cost_metrics JSON NOT NULL, 
    execution_metadata JSON NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    started_at TIMESTAMP WITH TIME ZONE, 
    completed_at TIMESTAMP WITH TIME ZONE, 
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    PRIMARY KEY (id), 
    UNIQUE (external_thread_id), 
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX ix_task_threads_external_thread_id ON task_threads (external_thread_id);

CREATE INDEX ix_task_threads_user_id ON task_threads (user_id);

CREATE INDEX ix_task_threads_organization_id ON task_threads (organization_id);

CREATE INDEX ix_task_threads_status ON task_threads (status);

CREATE TABLE connector_connections (
    id UUID NOT NULL, 
    organization_id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    connector_id VARCHAR(100) NOT NULL, 
    auth_type connector_auth_type NOT NULL, 
    status connector_status DEFAULT 'disconnected' NOT NULL, 
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

CREATE INDEX ix_connector_connections_user_id ON connector_connections (user_id);

CREATE INDEX ix_connector_connections_connector_id ON connector_connections (connector_id);

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
    UNIQUE (state), 
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
    FOREIGN KEY(organization_id) REFERENCES organizations (id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX ix_oauth_authorization_states_state ON oauth_authorization_states (state);

CREATE INDEX ix_oauth_authorization_states_user_id ON oauth_authorization_states (user_id);

CREATE INDEX ix_oauth_authorization_states_organization_id ON oauth_authorization_states (organization_id);

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

INSERT INTO alembic_version (version_num) VALUES ('20260812_01') RETURNING alembic_version.version_num;

COMMIT;
