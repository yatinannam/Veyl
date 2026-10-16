"""Job claiming gives up on jobs that keep crashing or hanging the worker.

A job whose worker died is reclaimed once it has been "running" longer than the stale
interval. Without a cap, a document that kills the worker every time would be retried
forever. Stale jobs that have used every attempt are now failed (with their document)
instead of being claimed again.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-16
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

UPGRADE = [
    "DROP FUNCTION ingestion_claim_job(interval)",
    """
    CREATE FUNCTION ingestion_claim_job(p_stale_after interval, p_max_attempts integer)
    RETURNS TABLE (job_id uuid, tenant_id uuid, document_id uuid, attempts integer)
    LANGUAGE sql VOLATILE SECURITY DEFINER
    SET search_path = public, pg_temp
    AS $$
        WITH abandoned AS (
            UPDATE ingestion_jobs AS s
            SET status = 'failed', last_error = 'ingestion did not complete',
                locked_at = NULL, updated_at = now()
            WHERE s.status = 'running'
              AND s.locked_at < now() - p_stale_after
              AND s.attempts >= p_max_attempts
            RETURNING s.tenant_id, s.document_id
        ), failed_documents AS (
            UPDATE documents AS d
            SET status = 'failed', error = 'ingestion did not complete', updated_at = now()
            FROM abandoned AS a
            WHERE d.tenant_id = a.tenant_id AND d.id = a.document_id
            RETURNING d.id
        )
        UPDATE ingestion_jobs AS j
        SET status = 'running', locked_at = now(), attempts = j.attempts + 1, updated_at = now()
        WHERE j.id = (
            SELECT q.id FROM ingestion_jobs AS q
            WHERE (q.status = 'queued' AND q.run_after <= now())
               OR (q.status = 'running'
                   AND q.locked_at < now() - p_stale_after
                   AND q.attempts < p_max_attempts)
            ORDER BY q.run_after
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        )
        RETURNING j.id, j.tenant_id, j.document_id, j.attempts
    $$
    """,
    "REVOKE ALL ON FUNCTION ingestion_claim_job(interval, integer) FROM PUBLIC",
    "GRANT EXECUTE ON FUNCTION ingestion_claim_job(interval, integer) TO veyl_app",
]

DOWNGRADE = [
    "DROP FUNCTION ingestion_claim_job(interval, integer)",
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


def upgrade() -> None:
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
