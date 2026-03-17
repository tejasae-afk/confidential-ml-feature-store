"""Tenant-scoped feature-set API routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from feature_store.middleware.tenant_auth import get_current_tenant
from feature_store.models.feature_schemas import FeatureSetCreate, FeatureSetResponse
from feature_store.models.tenant import Tenant
from feature_store.services.feature_service import FeatureService, get_feature_service

router = APIRouter(prefix="/features", tags=["features"])


@router.post("/", response_model=FeatureSetResponse, status_code=status.HTTP_201_CREATED)
async def create_feature_set(
    payload: FeatureSetCreate,
    tenant: Annotated[Tenant, Depends(get_current_tenant)],
    feature_service: Annotated[FeatureService, Depends(get_feature_service)],
) -> FeatureSetResponse:
    """Create a feature set for the authenticated tenant.

    Args:
        payload: Feature-set payload.
        tenant: Authenticated tenant context.
        feature_service: Feature service dependency.

    Returns:
        The created feature set.
    """
    return feature_service.create_feature_set(
        authenticated_tenant=tenant,
        payload=payload,
    )


@router.get("/{feature_set_name}", response_model=FeatureSetResponse)
async def get_feature_set(
    feature_set_name: str,
    tenant: Annotated[Tenant, Depends(get_current_tenant)],
    feature_service: Annotated[FeatureService, Depends(get_feature_service)],
    tenant_id: Annotated[str | None, Query()] = None,
) -> FeatureSetResponse:
    """Fetch a feature set for the authenticated tenant.

    Args:
        feature_set_name: Feature-set name.
        tenant: Authenticated tenant context.
        feature_service: Feature service dependency.
        tenant_id: Optional explicit tenant ID.

    Returns:
        The requested feature set.
    """
    return feature_service.get_feature_set(
        authenticated_tenant=tenant,
        feature_set_name=feature_set_name,
        requested_tenant_id=tenant_id,
    )


@router.get("/", response_model=list[FeatureSetResponse])
async def list_feature_sets(
    tenant: Annotated[Tenant, Depends(get_current_tenant)],
    feature_service: Annotated[FeatureService, Depends(get_feature_service)],
    tenant_id: Annotated[str | None, Query()] = None,
) -> list[FeatureSetResponse]:
    """List feature sets for the authenticated tenant.

    Args:
        tenant: Authenticated tenant context.
        feature_service: Feature service dependency.
        tenant_id: Optional explicit tenant ID.

    Returns:
        All feature sets visible to the tenant.
    """
    return feature_service.list_feature_sets(
        authenticated_tenant=tenant,
        requested_tenant_id=tenant_id,
    )


@router.delete("/{feature_set_name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_feature_set(
    feature_set_name: str,
    tenant: Annotated[Tenant, Depends(get_current_tenant)],
    feature_service: Annotated[FeatureService, Depends(get_feature_service)],
    tenant_id: Annotated[str | None, Query()] = None,
) -> Response:
    """Delete a feature set for the authenticated tenant.

    Args:
        feature_set_name: Feature-set name.
        tenant: Authenticated tenant context.
        feature_service: Feature service dependency.
        tenant_id: Optional explicit tenant ID.

    Returns:
        An empty success response.
    """
    feature_service.delete_feature_set(
        authenticated_tenant=tenant,
        feature_set_name=feature_set_name,
        requested_tenant_id=tenant_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
