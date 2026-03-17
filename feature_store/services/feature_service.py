"""Feature-store orchestration service."""

from __future__ import annotations

from typing import Any, Sequence

from fastapi import Request

from feature_store.models.feature_schemas import (
    FeatureSetCreate,
    FeatureSetResponse,
    InferenceRequest,
)
from feature_store.models.tenant import Tenant
from feature_store.services.dynamo_service import DynamoDBService
from feature_store.utils.exceptions import FeatureSetNotFound, ForbiddenAccess


class FeatureService:
    """Coordinates tenant validation and feature-store operations."""

    def __init__(self, dynamo_service: DynamoDBService) -> None:
        """Initialize the service.

        Args:
            dynamo_service: DynamoDB data-access service.
        """
        self._dynamo_service = dynamo_service

    def create_feature_set(
        self,
        *,
        authenticated_tenant: Tenant,
        payload: FeatureSetCreate,
    ) -> FeatureSetResponse:
        """Create a feature set for the authenticated tenant.

        Args:
            authenticated_tenant: Tenant extracted from API credentials.
            payload: Incoming feature-set payload.

        Returns:
            The stored feature set.

        Raises:
            ForbiddenAccess: If the tenant attempts to write another tenant's data.
        """
        self._ensure_tenant_access(
            authenticated_tenant.tenant_id,
            payload.tenant_id,
        )
        return self._dynamo_service.put_feature_set(payload)

    def get_feature_set(
        self,
        *,
        authenticated_tenant: Tenant,
        feature_set_name: str,
        requested_tenant_id: str | None = None,
    ) -> FeatureSetResponse:
        """Fetch a feature set for the authenticated tenant.

        Args:
            authenticated_tenant: Tenant extracted from API credentials.
            feature_set_name: Feature-set name to fetch.
            requested_tenant_id: Optional explicit tenant ID from the request.

        Returns:
            The stored feature set.
        """
        tenant_id = requested_tenant_id or authenticated_tenant.tenant_id
        self._ensure_tenant_access(authenticated_tenant.tenant_id, tenant_id)
        return self._dynamo_service.get_feature_set(tenant_id, feature_set_name)

    def list_feature_sets(
        self,
        *,
        authenticated_tenant: Tenant,
        requested_tenant_id: str | None = None,
    ) -> list[FeatureSetResponse]:
        """List feature sets for the authenticated tenant.

        Args:
            authenticated_tenant: Tenant extracted from API credentials.
            requested_tenant_id: Optional explicit tenant ID from the request.

        Returns:
            A list of feature sets.
        """
        tenant_id = requested_tenant_id or authenticated_tenant.tenant_id
        self._ensure_tenant_access(authenticated_tenant.tenant_id, tenant_id)
        return self._dynamo_service.list_feature_sets(tenant_id)

    def delete_feature_set(
        self,
        *,
        authenticated_tenant: Tenant,
        feature_set_name: str,
        requested_tenant_id: str | None = None,
    ) -> None:
        """Delete a feature set for the authenticated tenant.

        Args:
            authenticated_tenant: Tenant extracted from API credentials.
            feature_set_name: Feature-set name to delete.
            requested_tenant_id: Optional explicit tenant ID from the request.
        """
        tenant_id = requested_tenant_id or authenticated_tenant.tenant_id
        self._ensure_tenant_access(authenticated_tenant.tenant_id, tenant_id)
        self._dynamo_service.delete_feature_set(tenant_id, feature_set_name)

    def prepare_feature_vector(
        self,
        *,
        authenticated_tenant: Tenant,
        request_payload: InferenceRequest,
    ) -> list[float]:
        """Prepare a deterministic feature vector for inference.

        Args:
            authenticated_tenant: Tenant extracted from API credentials.
            request_payload: Inference request payload.

        Returns:
            A feature vector sorted by feature name.

        Raises:
            ForbiddenAccess: If the tenant attempts to use another tenant's data.
            FeatureSetNotFound: If the referenced feature set does not exist.
        """
        self._ensure_tenant_access(
            authenticated_tenant.tenant_id,
            request_payload.tenant_id,
        )
        feature_set = self._dynamo_service.get_feature_set(
            request_payload.tenant_id,
            request_payload.feature_set_name,
        )
        if not feature_set.features:
            raise FeatureSetNotFound(
                f"Feature set '{request_payload.feature_set_name}' has no features.",
            )
        return [value for _, value in sorted(feature_set.features.items(), key=lambda item: item[0])]

    def prepare_batch_feature_vectors(
        self,
        *,
        authenticated_tenant: Tenant,
        tenant_id: str,
        feature_set_names: Sequence[str],
    ) -> dict[str, list[float]]:
        """Batch-prepare multiple feature vectors for future inference fan-out.

        Args:
            authenticated_tenant: Tenant extracted from API credentials.
            tenant_id: Tenant that owns the requested feature sets.
            feature_set_names: Names of feature sets to fetch.

        Returns:
            A mapping of feature-set name to deterministic feature vector.
        """
        self._ensure_tenant_access(authenticated_tenant.tenant_id, tenant_id)
        feature_sets = self._dynamo_service.batch_get_feature_sets(tenant_id, feature_set_names)
        return {
            feature_set.feature_set_name: [
                value for _, value in sorted(feature_set.features.items(), key=lambda item: item[0])
            ]
            for feature_set in feature_sets
        }

    @staticmethod
    def _ensure_tenant_access(authenticated_tenant_id: str, requested_tenant_id: str) -> None:
        """Enforce tenant ownership for all service operations.

        Args:
            authenticated_tenant_id: Tenant derived from credentials.
            requested_tenant_id: Tenant targeted by the operation.

        Raises:
            ForbiddenAccess: If the tenant tries to access another tenant's data.
        """
        if authenticated_tenant_id != requested_tenant_id:
            raise ForbiddenAccess(
                "Tenant is not authorized to access the requested resource.",
            )


def get_feature_service(request: Request) -> FeatureService:
    """Resolve the feature service from application state.

    Args:
        request: Incoming FastAPI request.

    Returns:
        The configured ``FeatureService`` instance.
    """
    return request.app.state.feature_service  # type: ignore[no-any-return]
