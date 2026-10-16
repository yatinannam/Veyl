import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class AppError(Exception):
    """An error that maps to a structured HTTP response."""

    status_code = 500
    code = "internal_error"
    default_message = "Internal server error."

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.default_message
        super().__init__(self.message)


class Unauthenticated(AppError):
    status_code = 401
    code = "unauthenticated"
    default_message = "Authentication required."


class InvalidCredentials(AppError):
    status_code = 401
    code = "invalid_credentials"
    default_message = "Invalid workspace, email, or password."


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"
    default_message = "You do not have permission to perform this action."


class CsrfFailed(AppError):
    status_code = 403
    code = "csrf_failed"
    default_message = "CSRF token missing or invalid."


class NotFound(AppError):
    status_code = 404
    code = "not_found"
    default_message = "Resource not found."


class Conflict(AppError):
    status_code = 409
    code = "conflict"
    default_message = "Resource already exists."


class RateLimited(AppError):
    status_code = 429
    code = "rate_limited"
    default_message = "Too many attempts. Try again later."


class ServiceUnavailable(AppError):
    status_code = 503
    code = "service_unavailable"
    default_message = "A required service is unavailable."


_HTTP_CODES = {404: "not_found", 405: "method_not_allowed"}


def _trace_id(request: Request) -> str | None:
    return getattr(request.state, "trace_id", None)


def error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, Any]] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {"code": code, "message": message, "trace_id": _trace_id(request)}
    if details is not None:
        body["details"] = details
    return JSONResponse({"error": body}, status_code=status_code)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return error_response(request, exc.status_code, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Never echo submitted values back: they may be passwords.
        details = [
            {"loc": list(err["loc"]), "msg": err["msg"], "type": err["type"]}
            for err in exc.errors()
        ]
        return error_response(
            request, 422, "validation_error", "Request validation failed.", details
        )

    @app.exception_handler(OperationalError)
    async def handle_db_unavailable(request: Request, exc: OperationalError) -> JSONResponse:
        logger.warning("database unavailable (trace_id=%s)", _trace_id(request))
        return error_response(
            request, 503, ServiceUnavailable.code, ServiceUnavailable.default_message
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "http_error")
        message = exc.detail if isinstance(exc.detail, str) else "HTTP error."
        return error_response(request, exc.status_code, code, message)

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error (trace_id=%s)", _trace_id(request))
        return error_response(request, 500, "internal_error", "Internal server error.")
