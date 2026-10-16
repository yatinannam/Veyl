"""Tenants, users, sessions with row-level security.

Revision ID: 0001
Revises:
Create Date: 2026-10-16
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _rls(table: str, tenant_column: str) -> list[str]:
    """App role sees only the current tenant; the owner (migrations and SECURITY DEFINER
    functions) sees all rows. FORCE makes the policies apply to the owner as well."""
    predicate = f"{tenant_column} = app_current_tenant()"
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY {table}_tenant_isolation ON {table} FOR ALL TO veyl_app "
        f"USING ({predicate}) WITH CHECK ({predicate})",
        f"CREATE POLICY {table}_owner_all ON {table} FOR ALL TO veyl_owner "
        "USING (true) WITH CHECK (true)",
    ]


UPGRADE = [
    "CREATE TYPE user_role AS ENUM ('member', 'manager', 'admin')",
    # NULLIF: after a transaction-local setting has been used, Postgres reports ''
    # rather than NULL; ''::uuid would raise instead of matching nothing.
    """
    CREATE FUNCTION app_current_tenant() RETURNS uuid
    LANGUAGE sql STABLE
    AS $$ SELECT NULLIF(current_setting('app.tenant_id', true), '')::uuid $$
    """,
    "GRANT EXECUTE ON FUNCTION app_current_tenant() TO veyl_app",
    """
    CREATE TABLE tenants (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        slug text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'),
        name text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
        policy_version bigint NOT NULL DEFAULT 1,
        created_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE users (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
        email citext NOT NULL CHECK (length(email) BETWEEN 3 AND 320),
        password_hash text NOT NULL,
        role user_role NOT NULL,
        is_active boolean NOT NULL DEFAULT true,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (tenant_id, email),
        UNIQUE (tenant_id, id)
    )
    """,
    """
    CREATE TABLE sessions (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        tenant_id uuid NOT NULL,
        user_id uuid NOT NULL,
        token_hash bytea NOT NULL UNIQUE CHECK (octet_length(token_hash) = 32),
        created_at timestamptz NOT NULL DEFAULT now(),
        expires_at timestamptz NOT NULL,
        revoked_at timestamptz,
        FOREIGN KEY (tenant_id, user_id) REFERENCES users (tenant_id, id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX sessions_tenant_user_idx ON sessions (tenant_id, user_id)",
    *_rls("tenants", "id"),
    *_rls("users", "tenant_id"),
    *_rls("sessions", "tenant_id"),
    "GRANT SELECT, UPDATE (name, policy_version) ON tenants TO veyl_app",
    "GRANT SELECT, INSERT, UPDATE (role, is_active) ON users TO veyl_app",
    "GRANT SELECT, INSERT, UPDATE (revoked_at) ON sessions TO veyl_app",
]

DOWNGRADE = [
    "DROP TABLE sessions",
    "DROP TABLE users",
    "DROP TABLE tenants",
    "DROP FUNCTION app_current_tenant()",
    "DROP TYPE user_role",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
