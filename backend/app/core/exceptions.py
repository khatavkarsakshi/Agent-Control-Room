"""Application error hierarchy and global exception handlers.

Every error that escapes the API is rendered with the same envelope::

    {"error": {"code": "...", "message": "...", "details": ..., "request_id": "..."}}

Internal exception messages are never leaked in production.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings
from app.core.logging import request_id_var

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base class for expected, client-visible failures."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(
        self,
        message: str,
        *,
        details: Any = None,
        status_code: int | None = None,
        code: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details
        if status_code is not None:
            self.status_code = status_code
        if code is not None:
            self.code = code
        self.headers = headers or {}


class BadRequestError(AppError):
    status_code = 400
    code = "bad_request"


class UnauthorizedError(AppError):
    status_code = 401
    code = "unauthorized"


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class PayloadTooLargeError(AppError):
    status_code = 413
    code = "payload_too_large"


class RateLimitedError(AppError):
    status_code = 429
    code = "rate_limited"


class ProviderError(AppError):
    """An upstream LLM provider failed in a way the caller can act on."""

    status_code = 502
    code = "provider_error"


class ServiceUnavailableError(AppError):
    status_code = 503
    code = "service_unavailable"


def error_payload(
    *,
    code: str,
    message: str,
    details: Any = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details,
            "request_id": request_id or request_id_var.get(),
        }
    }


def register_exception_handlers(app: FastAPI) -> None:
    """Attach all handlers to ``app``."""

    @app.exception_handler(AppError)
    async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        if exc.status_code >= 500:
            logger.error("Application error: %s", exc.message, exc_info=exc)
        else:
            logger.info("Client error %s: %s", exc.code, exc.message)

        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(
                code=exc.code,
                message=exc.message,
                details=exc.details,
                request_id=getattr(request.state, "request_id", None),
            ),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # ``errors()`` can contain exception objects; strip them for JSON safety.
        details = [
            {
                "loc": list(error.get("loc", ())),
                "msg": error.get("msg", "invalid value"),
                "type": error.get("type", "value_error"),
            }
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=error_payload(
                code="validation_error",
                message="Request validation failed",
                details=details,
                request_id=getattr(request.state, "request_id", None),
            ),
        )

    @app.exception_handler(IntegrityError)
    async def _integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
        logger.warning("Database integrity error: %s", exc)
        return JSONResponse(
            status_code=409,
            content=error_payload(
                code="conflict",
                message="The request conflicts with existing data",
                request_id=getattr(request.state, "request_id", None),
            ),
        )

    @app.exception_handler(SQLAlchemyError)
    async def _database_error_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        logger.exception("Unhandled database error")
        return JSONResponse(
            status_code=500,
            content=error_payload(
                code="database_error",
                message="A database error occurred",
                request_id=getattr(request.state, "request_id", None),
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(
                code="http_error",
                message=str(exc.detail),
                request_id=getattr(request.state, "request_id", None),
            ),
            headers=getattr(exc, "headers", None) or {},
        )

    @app.exception_handler(Exception)
    async def _unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
        message = str(exc) if settings.debug else "An unexpected error occurred"
        return JSONResponse(
            status_code=500,
            content=error_payload(
                code="internal_error",
                message=message,
                request_id=getattr(request.state, "request_id", None),
            ),
        )
