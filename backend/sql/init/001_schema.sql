CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS users (
    id VARCHAR(36) PRIMARY KEY DEFAULT gen_random_uuid()::text,
    email VARCHAR(255) UNIQUE NOT NULL,
    username VARCHAR(80) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_users_email ON users(email);

CREATE TABLE IF NOT EXISTS documents (
    id VARCHAR(36) PRIMARY KEY DEFAULT gen_random_uuid()::text,
    title VARCHAR(255) NOT NULL DEFAULT 'Untitled document',
    content_delta JSONB NOT NULL DEFAULT '{"ops":[{"insert":"\n"}]}'::jsonb,
    content_text TEXT NOT NULL DEFAULT '',
    owner_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    version INTEGER NOT NULL DEFAULT 0,
    vector_clock JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_documents_owner_id ON documents(owner_id);

CREATE TABLE IF NOT EXISTS document_permissions (
    id VARCHAR(36) PRIMARY KEY DEFAULT gen_random_uuid()::text,
    document_id VARCHAR(36) NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL DEFAULT 'viewer',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_document_user_permission UNIQUE(document_id, user_id),
    CONSTRAINT ck_document_permission_role CHECK(role IN ('owner', 'editor', 'viewer'))
);
CREATE INDEX IF NOT EXISTS ix_document_permissions_document_id ON document_permissions(document_id);
CREATE INDEX IF NOT EXISTS ix_document_permissions_user_id ON document_permissions(user_id);

CREATE TABLE IF NOT EXISTS document_operations (
    id VARCHAR(36) PRIMARY KEY DEFAULT gen_random_uuid()::text,
    document_id VARCHAR(36) NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    user_id VARCHAR(36) REFERENCES users(id) ON DELETE SET NULL,
    client_op_id VARCHAR(120),
    operation_delta JSONB NOT NULL,
    transformed_delta JSONB NOT NULL,
    vector_clock JSONB NOT NULL DEFAULT '{}'::jsonb,
    base_version INTEGER NOT NULL,
    server_version INTEGER NOT NULL,
    causal_relation VARCHAR(30) NOT NULL DEFAULT 'unknown',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_document_server_version UNIQUE(document_id, server_version)
);
CREATE INDEX IF NOT EXISTS ix_document_operations_doc_version ON document_operations(document_id, server_version);
CREATE INDEX IF NOT EXISTS ix_document_operations_document_id ON document_operations(document_id);
CREATE INDEX IF NOT EXISTS ix_document_operations_user_id ON document_operations(user_id);
CREATE INDEX IF NOT EXISTS ix_document_operations_doc_client_op ON document_operations(document_id, client_op_id);
CREATE INDEX IF NOT EXISTS ix_document_operations_vector_clock ON document_operations USING GIN(vector_clock);

CREATE TABLE IF NOT EXISTS document_versions (
    id VARCHAR(36) PRIMARY KEY DEFAULT gen_random_uuid()::text,
    document_id VARCHAR(36) NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    content_delta JSONB NOT NULL,
    content_text TEXT NOT NULL DEFAULT '',
    vector_clock JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by VARCHAR(36) REFERENCES users(id) ON DELETE SET NULL,
    operation_id VARCHAR(36) REFERENCES document_operations(id) ON DELETE SET NULL,
    action VARCHAR(30) NOT NULL DEFAULT 'edit',
    source_version INTEGER,
    target_version INTEGER,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_document_version UNIQUE(document_id, version)
);
CREATE INDEX IF NOT EXISTS ix_document_versions_doc_version ON document_versions(document_id, version);
CREATE INDEX IF NOT EXISTS ix_document_versions_created_by ON document_versions(created_by);

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_documents_updated_at ON documents;
CREATE TRIGGER trg_documents_updated_at
BEFORE UPDATE ON documents
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();
