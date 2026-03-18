"""Inference routes backed by the enclave inference service."""

from __future__ import annotations

import os
from pathlib import Path
from time import perf_counter
from typing import Annotated

from fastapi import APIRouter, Depends

from feature_store.middleware.tenant_auth import get_current_tenant
from feature_store.models.feature_schemas import InferenceRequest, InferenceResponse
from feature_store.models.tenant import Tenant
from feature_store.services.enclave_client import (
    EnclaveClient,
    MockEnclaveClient,
    get_enclave_client,
)
from feature_store.services.feature_service import FeatureService, get_feature_service
from feature_store.utils.exceptions import AppException, EnclaveCommunicationError
from feature_store.utils.logger import get_logger

router = APIRouter(prefix="/inference", tags=["inference"])
logger = get_logger(__name__)


class ModelArtifactNotFoundError(AppException):
    """Raised when encrypted model artifacts are not available."""

    status_code = 404
    error_code = "model_artifact_not_found"


class ModelArtifactInvalidError(AppException):
    """Raised when model artifacts are malformed."""

    status_code = 400
    error_code = "invalid_model_artifact"


@router.post("/", response_model=InferenceResponse)
async def run_inference(
    payload: InferenceRequest,
    tenant: Annotated[Tenant, Depends(get_current_tenant)],
    feature_service: Annotated[FeatureService, Depends(get_feature_service)],
    enclave_client: Annotated[EnclaveClient | MockEnclaveClient, Depends(get_enclave_client)],
) -> InferenceResponse:
    """Trigger enclave-backed inference for a tenant-owned feature set.

    Args:
        payload: Inference request payload.
        tenant: Authenticated tenant context.
        feature_service: Feature store orchestrator.
        enclave_client: Real or mock enclave client.

    Returns:
        The normalized inference response.
    """
    total_started = perf_counter()

    dynamodb_started = perf_counter()
    feature_vector = feature_service.prepare_feature_vector(
        authenticated_tenant=tenant,
        request_payload=payload,
    )
    dynamodb_ms = (perf_counter() - dynamodb_started) * 1000.0

    encrypted_model_blob, encrypted_model_key = _load_model_artifacts(
        tenant.tenant_id,
        payload.model_name,
    )

    enclave_started = perf_counter()
    enclave_response = enclave_client.predict(
        payload.model_name,
        feature_vector,
        encrypted_model_key,
        encrypted_model_blob=encrypted_model_blob,
        tenant_id=tenant.tenant_id,
    )
    enclave_round_trip_ms = (perf_counter() - enclave_started) * 1000.0
    total_ms = (perf_counter() - total_started) * 1000.0

    logger.info(
        "inference_completed model_name=%s dynamodb_ms=%.3f enclave_round_trip_ms=%.3f "
        "enclave_compute_ms=%.3f total_ms=%.3f served_from_cache=%s",
        payload.model_name,
        dynamodb_ms,
        enclave_round_trip_ms,
        float(enclave_response["latency_ms"]),
        total_ms,
        bool(enclave_response.get("served_from_cache", False)),
    )

    return InferenceResponse(
        prediction=float(enclave_response["prediction"]),
        confidence=float(enclave_response["confidence"]),
        latency_ms=total_ms,
        served_from_cache=bool(enclave_response.get("served_from_cache", False)),
    )


@router.get("/models")
async def list_available_models(
    tenant: Annotated[Tenant, Depends(get_current_tenant)],
    enclave_client: Annotated[EnclaveClient | MockEnclaveClient, Depends(get_enclave_client)],
) -> dict[str, list[str]]:
    """List locally available and currently loaded models for the tenant.

    Args:
        tenant: Authenticated tenant context.
        enclave_client: Real or mock enclave client.

    Returns:
        Stored and loaded model names.
    """
    available_models = sorted(_list_stored_models(tenant.tenant_id))
    try:
        loaded_models = sorted(enclave_client.list_models())
    except EnclaveCommunicationError:
        loaded_models = []
    return {
        "available_models": available_models,
        "loaded_models": loaded_models,
    }


def _load_model_artifacts(tenant_id: str, model_name: str) -> tuple[bytes, bytes]:
    """Load encrypted model artifacts from local storage.

    Expected layout:
    ``<MODEL_STORAGE_DIR>/<tenant_id>/<model_name>/encrypted_model.bin``
    ``<MODEL_STORAGE_DIR>/<tenant_id>/<model_name>/encrypted_data_key.bin``

    Args:
        tenant_id: Tenant identifier.
        model_name: Model identifier.

    Returns:
        Tuple of ``(encrypted_model_blob, encrypted_data_key)``.

    Raises:
        ModelArtifactNotFoundError: If either file is missing.
        ModelArtifactInvalidError: If any artifact is empty.
    """
    model_directory = _model_storage_root() / tenant_id / model_name
    encrypted_model_path = model_directory / "encrypted_model.bin"
    encrypted_key_path = model_directory / "encrypted_data_key.bin"

    if not encrypted_model_path.is_file() or not encrypted_key_path.is_file():
        raise ModelArtifactNotFoundError(
            f"Encrypted model artifacts for '{model_name}' were not found "
            f"for tenant '{tenant_id}'.",
        )

    encrypted_model_blob = encrypted_model_path.read_bytes()
    encrypted_model_key = encrypted_key_path.read_bytes()
    if not encrypted_model_blob or not encrypted_model_key:
        raise ModelArtifactInvalidError(
            f"Encrypted model artifacts for '{model_name}' are empty or invalid.",
        )
    return encrypted_model_blob, encrypted_model_key


def _list_stored_models(tenant_id: str) -> list[str]:
    """List models that have encrypted artifacts on the host.

    Args:
        tenant_id: Tenant identifier.

    Returns:
        Model directory names.
    """
    tenant_dir = _model_storage_root() / tenant_id
    if not tenant_dir.exists():
        return []
    return [
        path.name
        for path in tenant_dir.iterdir()
        if path.is_dir()
        and (path / "encrypted_model.bin").is_file()
        and (path / "encrypted_data_key.bin").is_file()
    ]


def _model_storage_root() -> Path:
    """Return the model artifact root directory.

    Returns:
        Model storage root path.
    """
    return Path(os.getenv("MODEL_STORAGE_DIR", "artifacts")).resolve()
