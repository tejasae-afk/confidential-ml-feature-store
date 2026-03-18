"""Structured logging utilities for the feature store."""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

_request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)
_tenant_id_context: ContextVar[str | None] = ContextVar("tenant_id", default=None)


class RequestContextFilter(logging.Filter):
    """Inject request-scoped context into log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Populate request and tenant context.

        Args:
            record: The record being emitted.

        Returns:
            ``True`` to keep the record.
        """
        record.request_id = _request_id_context.get()
        record.tenant_id = _tenant_id_context.get()
        return True


class JsonFormatter(logging.Formatter):
    """Serialize log records into JSON."""

    def format(self, record: logging.LogRecord) -> str:
        """Format the record as JSON.

        Args:
            record: The log record.

        Returns:
            A JSON-encoded log line.
        """
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        request_id = getattr(record, "request_id", None)
        if request_id:
            payload["request_id"] = request_id

        tenant_id = getattr(record, "tenant_id", None)
        if tenant_id:
            payload["tenant_id"] = tenant_id

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def set_log_context(*, request_id: str | None = None, tenant_id: str | None = None) -> None:
    """Set request-scoped logging context.

    Args:
        request_id: Request identifier to inject into logs.
        tenant_id: Tenant identifier to inject into logs.
    """
    if request_id is not None:
        _request_id_context.set(request_id)
    if tenant_id is not None:
        _tenant_id_context.set(tenant_id)


def clear_log_context() -> None:
    """Clear request-scoped logging context."""
    _request_id_context.set(None)
    _tenant_id_context.set(None)


def get_request_id() -> str | None:
    """Return the current request ID from context.

    Returns:
        The request ID if one is set.
    """
    return _request_id_context.get()


def get_tenant_id() -> str | None:
    """Return the current tenant ID from context.

    Returns:
        The tenant ID if one is set.
    """
    return _tenant_id_context.get()


def configure_logging(level: str) -> None:
    """Configure root logging with structured JSON output.

    Args:
        level: Desired log level.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RequestContextFilter())

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.filters.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level.upper())

    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        logger.propagate = True
        logger.setLevel(level.upper())

    logging.captureWarnings(True)


def get_logger(name: str) -> logging.Logger:
    """Return a configured logger.

    Args:
        name: Logger name.

    Returns:
        A ``logging.Logger`` instance.
    """
    return logging.getLogger(name)
