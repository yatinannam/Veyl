"""Conversations, messages, and the append-only audit log.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-16
"""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def _rls(table: str) -> list[str]:
    predicate = "tenant_id = app_current_tenant()"
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY {table}_tenant_isolation ON {table} FOR ALL TO veyl_app "
        f"USING ({predicate}) WITH CHECK ({predicate})",
        f"CREATE POLICY {table}_owner_all ON {table} FOR ALL TO veyl_owner "
        "USING (true) WITH CHECK (true)",
    ]


UPGRADE = [
    "CREATE TYPE msg_role AS ENUM ('user', 'assistant')",
    "CREATE TYPE audit_decision AS ENUM ('allow', 'deny', 'error')",
    "ALTER TABLE tenants ADD COLUMN audit_query_mode text NOT NULL DEFAULT 'hash' "
    "CHECK (audit_query_mode IN ('hash', 'plaintext'))",
    "GRANT UPDATE (audit_query_mode) ON tenants TO veyl_app",
    """
    CREATE TABLE conversations (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL,
        user_id uuid NOT NULL,
        title text NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, id),
        FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX conversations_owner_idx ON conversations (tenant_id, user_id, updated_at DESC)",
    """
    CREATE TABLE messages (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL,
        conversation_id uuid NOT NULL,
        role msg_role NOT NULL,
        content text NOT NULL,
        cited_chunk_ids uuid[] NOT NULL DEFAULT '{}',
        cited_document_ids uuid[] NOT NULL DEFAULT '{}',
        trace_id uuid NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        FOREIGN KEY (tenant_id, conversation_id) REFERENCES conversations (tenant_id, id)
            ON DELETE CASCADE
    )
    """,
    "CREATE INDEX messages_conversation_idx ON messages (conversation_id, created_at)",
    """
    CREATE TABLE audit_events (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES tenants (id),
        occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        actor_user_id uuid,
        event_type text NOT NULL CHECK (event_type ~ '^[a-z_]+\\.[a-z_]+$'),
        decision audit_decision NOT NULL DEFAULT 'allow',
        query_ref text,
        query_text text,
        retrieved_chunk_ids uuid[] NOT NULL DEFAULT '{}',
        retrieved_document_ids uuid[] NOT NULL DEFAULT '{}',
        result_count integer,
        trace_id uuid,
        model text,
        details jsonb NOT NULL DEFAULT '{}'
    )
    """,
    "CREATE INDEX audit_events_tenant_time_idx ON audit_events "
    "(tenant_id, occurred_at DESC, id DESC)",
    "CREATE INDEX audit_events_actor_idx ON audit_events "
    "(tenant_id, actor_user_id, occurred_at DESC)",
    "CREATE INDEX audit_events_documents_idx ON audit_events USING gin (retrieved_document_ids)",
    """
    CREATE FUNCTION audit_events_immutable() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
    BEGIN
        RAISE EXCEPTION 'audit_events is append-only' USING ERRCODE = 'insufficient_privilege';
    END
    $$
    """,
    "CREATE TRIGGER audit_events_no_update_delete BEFORE UPDATE OR DELETE ON audit_events "
    "FOR EACH ROW EXECUTE FUNCTION audit_events_immutable()",
    "CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE ON audit_events "
    "FOR EACH STATEMENT EXECUTE FUNCTION audit_events_immutable()",
    *_rls("conversations"),
    *_rls("messages"),
    *_rls("audit_events"),
    "GRANT SELECT, INSERT, DELETE, UPDATE (title, updated_at) ON conversations TO veyl_app",
    "GRANT SELECT, INSERT ON messages TO veyl_app",
    "GRANT SELECT, INSERT ON audit_events TO veyl_app",
]

DOWNGRADE = [
    "DROP TABLE audit_events",
    "DROP FUNCTION audit_events_immutable()",
    "DROP TABLE messages",
    "DROP TABLE conversations",
    "ALTER TABLE tenants DROP COLUMN audit_query_mode",
    "DROP TYPE audit_decision",
    "DROP TYPE msg_role",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
