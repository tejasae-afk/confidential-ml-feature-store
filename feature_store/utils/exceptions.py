"""Custom exceptions and FastAPI handlers."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from feature_store.utils.logger import get_logger

logger = get_logger(__name__)


class AppException(Exception):
    """Base exception for controlled application errors."""

    status_code = status.HTTP_400_BAD_REQUEST
    error_code = "application_error"

    def __init__(self, detail: str) -> None:
        """Initialize the exception.

        Args:
            detail: Human-readable error description.
        """
        super().__init__(detail)
        self.detail = detail


class TenantNotFound(AppException):
    """Raised when a tenant record does not exist."""

    status_code = status.HTTP_404_NOT_FOUND
    error_code = "tenant_not_found"


class FeatureSetNotFound(AppException):
    """Raised when a feature set does not exist."""

    status_code = status.HTTP_404_NOT_FOUND
    error_code = "feature_set_not_found"


class UnauthorizedAccess(AppException):
    """Raised when authentication headers are missing or invalid."""

    status_code = status.HTTP_401_UNAUTHORIZED
    error_code = "unauthorized_access"


class ForbiddenAccess(AppException):
    """Raised when an authenticated tenant accesses another tenant's resources."""

    status_code = status.HTTP_403_FORBIDDEN
    error_code = "forbidden_access"


class FeatureSetConflict(AppException):
    """Raised when a feature set already exists."""

    status_code = status.HTTP_409_CONFLICT
    error_code = "feature_set_conflict"


class DataStoreError(AppException):
    """Raised when the backing data store fails unexpectedly."""

    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    error_code = "data_store_error"


class EnclaveCommunicationError(AppException):
    """Raised when future enclave communication fails."""

    status_code = status.HTTP_502_BAD_GATEWAY
    error_code = "enclave_communication_error"


class ServiceNotImplementedError(AppException):
    """Raised when accessing a scaffolded but unimplemented endpoint."""

    status_code = status.HTTP_501_NOT_IMPLEMENTED
    error_code = "service_not_implemented"


def _error_payload(request: Request, error_code: str, detail: str) -> dict[str, object]:
    """Build a standardized error payload.

    Args:
        request: Incoming request object.
        error_code: Stable machine-readable error code.
        detail: Human-readable error message.

    Returns:
        A serializable error payload.
    """
    return {
        "error": error_code,
        "detail": detail,
        "request_id": getattr(request.state, "request_id", None),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def register_exception_handlers(app: FastAPI) -> None:
    """Register exception handlers on the FastAPI app.

    Args:
        app: FastAPI application instance.
    """

    @app.exception_handler(AppException)
    async def handle_app_exception(request: Request, exc: AppException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_payload(request, exc.error_code, exc.detail),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        logger.warning("Request validation failed: %s", exc.errors())
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_error_payload(
                request,
                "validation_error",
                "Request validation failed.",
            ),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled internal error")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error_payload(
                request,
                "internal_server_error",
                "An internal server error occurred.",
            ),
        )
