"""Inference trigger endpoint."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status

from feature_store.middleware.tenant_auth import get_current_tenant
from feature_store.models.feature_schemas import InferenceRequest, InferenceResponse
from feature_store.models.tenant import Tenant
from feature_store.utils.exceptions import ServiceNotImplementedError

router = APIRouter(prefix="/v1/inference", tags=["inference"])


@router.post("", response_model=InferenceResponse, status_code=status.HTTP_501_NOT_IMPLEMENTED)
async def run_inference(
    payload: InferenceRequest,
    tenant: Annotated[Tenant, Depends(get_current_tenant)],
) -> InferenceResponse:
    """Trigger enclave-backed inference for a tenant-scoped entity."""
    raise ServiceNotImplementedError(
        "Inference endpoint is scaffolded but not implemented yet.",
    )
