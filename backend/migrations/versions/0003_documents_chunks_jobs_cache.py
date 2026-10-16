"""Documents, chunks with denormalised ACLs and embeddings, ingestion jobs, retrieval
cache; job-claim function.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-16
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def _rls(table: str) -> list[str]:
    """App role sees only the current tenant; the owner (migrations and SECURITY DEFINER
    functions) sees all rows. FORCE makes the policies apply to the owner as well."""
    predicate = "tenant_id = app_current_tenant()"
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY {table}_tenant_isolation ON {table} FOR ALL TO veyl_app "
        f"USING ({predicate}) WITH CHECK ({predicate})",
        f"CREATE POLICY {table}_owner_all ON {table} FOR ALL TO veyl_owner "
        "USING (true) WITH CHECK (true)",
    ]


ROLES_CHECK = "allowed_roles <@ ARRAY['member', 'manager', 'admin']::text[]"

UPGRADE = [
    "CREATE TYPE classification AS ENUM ('public', 'internal', 'confidential', 'restricted')",
    "CREATE TYPE doc_status AS ENUM ('pending', 'processing', 'ready', 'failed')",
    "CREATE TYPE job_status AS ENUM ('queued', 'running', 'done', 'failed')",
    f"""
    CREATE TABLE documents (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
        uploaded_by uuid NOT NULL,
        title text NOT NULL CHECK (length(title) BETWEEN 1 AND 300),
        original_filename text NOT NULL CHECK (length(original_filename) BETWEEN 1 AND 255),
        mime_type text NOT NULL,
        size_bytes integer NOT NULL CHECK (size_bytes > 0),
        sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{{64}}$'),
        storage_key text NOT NULL,
        classification classification NOT NULL,
        allowed_roles text[] NOT NULL DEFAULT '{{}}' CHECK ({ROLES_CHECK}),
        allowed_user_ids uuid[] NOT NULL DEFAULT '{{}}',
        acl_version integer NOT NULL DEFAULT 1,
        status doc_status NOT NULL DEFAULT 'pending',
        error text,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        deleted_at timestamptz,
        UNIQUE (tenant_id, id),
        FOREIGN KEY (tenant_id, uploaded_by) REFERENCES users (tenant_id, id)
    )
    """,
    "CREATE INDEX documents_tenant_created_idx ON documents (tenant_id, created_at DESC)",
    f"""
    CREATE TABLE document_chunks (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL,
        document_id uuid NOT NULL,
        chunk_index integer NOT NULL CHECK (chunk_index >= 0),
        content text NOT NULL,
        embedding vector(384) NOT NULL,
        classification classification NOT NULL,
        allowed_roles text[] NOT NULL CHECK ({ROLES_CHECK}),
        allowed_user_ids uuid[] NOT NULL,
        acl_version integer NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (document_id, chunk_index),
        FOREIGN KEY (tenant_id, document_id) REFERENCES documents (tenant_id, id)
            ON DELETE CASCADE
    )
    """,
    "CREATE INDEX document_chunks_embedding_idx ON document_chunks "
    "USING hnsw (embedding vector_cosine_ops)",
    "CREATE INDEX document_chunks_tenant_doc_idx ON document_chunks (tenant_id, document_id)",
    "CREATE INDEX document_chunks_roles_idx ON document_chunks USING gin (allowed_roles)",
    "CREATE INDEX document_chunks_users_idx ON document_chunks USING gin (allowed_user_ids)",
    """
    CREATE TABLE ingestion_jobs (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL,
        document_id uuid NOT NULL,
        status job_status NOT NULL DEFAULT 'queued',
        attempts integer NOT NULL DEFAULT 0,
        run_after timestamptz NOT NULL DEFAULT now(),
        locked_at timestamptz,
        last_error text,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        FOREIGN KEY (tenant_id, document_id) REFERENCES documents (tenant_id, id)
            ON DELETE CASCADE
    )
    """,
    "CREATE INDEX ingestion_jobs_queue_idx ON ingestion_jobs (status, run_after)",
    """
    CREATE TABLE retrieval_cache (
        cache_key bytea PRIMARY KEY CHECK (octet_length(cache_key) = 32),
        tenant_id uuid NOT NULL,
        user_id uuid NOT NULL,
        policy_version bigint NOT NULL,
        chunk_ids uuid[] NOT NULL,
        scores double precision[] NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        expires_at timestamptz NOT NULL,
        FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX retrieval_cache_expires_idx ON retrieval_cache (expires_at)",
    *_rls("documents"),
    *_rls("document_chunks"),
    *_rls("ingestion_jobs"),
    *_rls("retrieval_cache"),
    "GRANT SELECT, INSERT, UPDATE (title, classification, allowed_roles, allowed_user_ids, "
    "acl_version, status, error, updated_at, deleted_at) ON documents TO veyl_app",
    "GRANT SELECT, INSERT, DELETE, UPDATE (classification, allowed_roles, allowed_user_ids, "
    "acl_version) ON document_chunks TO veyl_app",
    "GRANT SELECT, INSERT, UPDATE (status, attempts, run_after, locked_at, last_error, "
    "updated_at) ON ingestion_jobs TO veyl_app",
    "GRANT SELECT, INSERT, DELETE, UPDATE (policy_version, chunk_ids, scores, created_at, "
    "expires_at) ON retrieval_cache TO veyl_app",
    # The worker must find work in any tenant before it knows which tenant to scope to.
    """
    CREATE FUNCTION ingestion_claim_job(p_stale_after interval)
    RETURNS TABLE (job_id uuid, tenant_id uuid, document_id uuid, attempts integer)
    LANGUAGE sql VOLATILE SECURITY DEFINER
    SET search_path = public, pg_temp
    AS $$
        UPDATE ingestion_jobs AS j
        SET status = 'running', locked_at = now(), attempts = j.attempts + 1, updated_at = now()
        WHERE j.id = (
            SELECT q.id FROM ingestion_jobs AS q
            WHERE (q.status = 'queued' AND q.run_after <= now())
               OR (q.status = 'running' AND q.locked_at < now() - p_stale_after)
            ORDER BY q.run_after
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        )
        RETURNING j.id, j.tenant_id, j.document_id, j.attempts
    $$
    """,
    "REVOKE ALL ON FUNCTION ingestion_claim_job(interval) FROM PUBLIC",
    "GRANT EXECUTE ON FUNCTION ingestion_claim_job(interval) TO veyl_app",
]

DOWNGRADE = [
    "DROP FUNCTION ingestion_claim_job(interval)",
    "DROP TABLE retrieval_cache",
    "DROP TABLE ingestion_jobs",
    "DROP TABLE document_chunks",
    "DROP TABLE documents",
    "DROP TYPE job_status",
    "DROP TYPE doc_status",
    "DROP TYPE classification",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
