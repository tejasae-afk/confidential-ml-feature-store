"""Tenant authentication dependency."""

from __future__ import annotations

from secrets import compare_digest
from typing import Annotated

from fastapi import Header, Request

from feature_store.models.tenant import Tenant
from feature_store.services.dynamo_service import DynamoDBService
from feature_store.utils.exceptions import TenantNotFound, UnauthorizedAccess
from feature_store.utils.logger import set_log_context


async def get_current_tenant(
    request: Request,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> Tenant:
    """Authenticate the current request and return tenant context.

    Args:
        request: Incoming FastAPI request.
        x_tenant_id: Tenant ID provided in the request header.
        x_api_key: API key provided in the request header.

    Returns:
        The authenticated tenant model.

    Raises:
        UnauthorizedAccess: If credentials are missing or invalid.
    """
    normalized_tenant_id = (x_tenant_id or "").strip()
    provided_api_key = (x_api_key or "").strip()

    if not normalized_tenant_id or not provided_api_key:
        raise UnauthorizedAccess(
            "Missing required authentication headers: X-Tenant-ID and X-API-Key.",
        )

    dynamo_service = _get_dynamo_service(request)
    try:
        tenant = dynamo_service.get_tenant_record(normalized_tenant_id)
    except TenantNotFound as exc:
        raise UnauthorizedAccess("Invalid tenant credentials.") from exc

    if not tenant.is_active:
        raise UnauthorizedAccess("Tenant account is inactive.")

    if not compare_digest(tenant.api_key, provided_api_key):
        raise UnauthorizedAccess("Invalid tenant credentials.")

    request.state.tenant = tenant
    request.state.tenant_id = tenant.tenant_id
    set_log_context(tenant_id=tenant.tenant_id)
    return tenant


async def get_optional_tenant(request: Request) -> Tenant | None:
    """Return request-scoped tenant context if already authenticated.

    Args:
        request: Incoming FastAPI request.

    Returns:
        The authenticated tenant, if one is attached to request state.
    """
    return getattr(request.state, "tenant", None)


def _get_dynamo_service(request: Request) -> DynamoDBService:
    """Resolve the DynamoDB service from app state.

    Args:
        request: Incoming FastAPI request.

    Returns:
        The configured ``DynamoDBService``.
    """
    dynamo_service: DynamoDBService = request.app.state.dynamo_service
    return dynamo_service
