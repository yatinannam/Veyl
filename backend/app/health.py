import logging

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

READINESS_QUERY = "SELECT 1"
# A database that accepts connections but stalls must not hang the probe.
READINESS_TIMEOUT_MS = 2000


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness: the process is up. Does not touch dependencies."""
    return {"status": "ok"}


@router.get("/readyz")
def readyz(request: Request, response: Response) -> dict[str, str]:
    """Readiness: dependencies are reachable. Fails closed with 503."""
    engine: Engine = request.app.state.engine
    try:
        with engine.begin() as conn:
            conn.execute(text(f"SET LOCAL statement_timeout = {READINESS_TIMEOUT_MS}"))
            conn.execute(text(READINESS_QUERY))
    except SQLAlchemyError:
        logger.warning("readiness check failed: database unreachable")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unavailable"}
    return {"status": "ready"}
