"""Cross-tenant lookups for authentication, as narrow SECURITY DEFINER functions.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-16
"""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

UPGRADE = [
    """
    CREATE FUNCTION auth_resolve_login(p_tenant_slug text, p_email text)
    RETURNS TABLE (
        user_id uuid, tenant_id uuid, password_hash text, role user_role, is_active boolean
    )
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path = public, pg_temp
    AS $$
        SELECT u.id, u.tenant_id, u.password_hash, u.role, u.is_active
        FROM users u
        JOIN tenants t ON t.id = u.tenant_id
        WHERE t.slug = p_tenant_slug AND u.email = p_email::citext
    $$
    """,
    "REVOKE ALL ON FUNCTION auth_resolve_login(text, text) FROM PUBLIC",
    "GRANT EXECUTE ON FUNCTION auth_resolve_login(text, text) TO veyl_app",
    """
    CREATE FUNCTION auth_resolve_session(p_token_hash bytea)
    RETURNS TABLE (session_id uuid, user_id uuid, tenant_id uuid, role user_role)
    LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path = public, pg_temp
    AS $$
        SELECT s.id, u.id, u.tenant_id, u.role
        FROM sessions s
        JOIN users u ON u.tenant_id = s.tenant_id AND u.id = s.user_id
        WHERE s.token_hash = p_token_hash
          AND s.revoked_at IS NULL
          AND s.expires_at > now()
          AND u.is_active
    $$
    """,
    "REVOKE ALL ON FUNCTION auth_resolve_session(bytea) FROM PUBLIC",
    "GRANT EXECUTE ON FUNCTION auth_resolve_session(bytea) TO veyl_app",
]

DOWNGRADE = [
    "DROP FUNCTION auth_resolve_session(bytea)",
    "DROP FUNCTION auth_resolve_login(text, text)",
]


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
