"""DynamoDB data-access layer for tenant and feature-set records."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from time import sleep
from typing import Any, Final, Sequence, cast

import boto3
from boto3.dynamodb.conditions import Attr, Key
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import BotoCoreError, ClientError

from feature_store.models.feature_schemas import FeatureSetCreate, FeatureSetResponse
from feature_store.models.tenant import Tenant
from feature_store.utils.exceptions import (
    DataStoreError,
    FeatureSetConflict,
    FeatureSetNotFound,
    TenantNotFound,
)
from feature_store.utils.logger import get_logger

logger = get_logger(__name__)

_FEATURE_PREFIX: Final[str] = "FEATURE_SET#"
_TENANT_PREFIX: Final[str] = "TENANT#"
_BATCH_RETRY_LIMIT: Final[int] = 3


class DynamoDBService:
    """Encapsulates tenant-scoped DynamoDB access.

    The table uses ``tenant_id`` as the partition key and ``resource_id`` as the
    sort key. Feature-set items use a ``FEATURE_SET#`` prefix while tenant records
    use a ``TENANT#`` prefix.
    """

    def __init__(
        self,
        *,
        table_name: str,
        region_name: str,
        endpoint_url: str | None = None,
    ) -> None:
        """Initialize the service.

        Args:
            table_name: DynamoDB table name.
            region_name: AWS region for boto3.
            endpoint_url: Optional endpoint for DynamoDB Local.
        """
        self._resource = cast(
            Any,
            boto3.resource(
                "dynamodb",
                region_name=region_name,
                endpoint_url=endpoint_url,
            ),
        )
        self._table = cast(Any, self._resource.Table(table_name))
        self._client = self._table.meta.client
        self._serializer = TypeSerializer()
        self._deserializer = TypeDeserializer()
        self._table_name = table_name

    @property
    def table_name(self) -> str:
        """Return the configured table name.

        Returns:
            The table name.
        """
        return self._table_name

    def check_connectivity(self) -> bool:
        """Check whether the configured table is reachable.

        Returns:
            ``True`` when DynamoDB responds successfully, otherwise ``False``.
        """
        try:
            self._table.load()
            return True
        except (BotoCoreError, ClientError):
            logger.exception("DynamoDB connectivity check failed")
            return False

    def put_feature_set(self, feature_set: FeatureSetCreate) -> FeatureSetResponse:
        """Create a new tenant-scoped feature set.

        Args:
            feature_set: The feature set to store.

        Returns:
            The persisted feature-set representation.

        Raises:
            FeatureSetConflict: If the feature set already exists for the tenant.
            DataStoreError: If DynamoDB returns an unexpected error.
        """
        now = datetime.now(timezone.utc)
        item = {
            "tenant_id": feature_set.tenant_id,
            "resource_id": self._feature_resource_id(feature_set.feature_set_name),
            "entity_type": "FEATURE_SET",
            "feature_set_name": feature_set.feature_set_name,
            "features": self._to_decimal(feature_set.features),
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "version": 1,
        }

        try:
            self._table.put_item(
                Item=item,
                ConditionExpression=(
                    Attr("tenant_id").not_exists() & Attr("resource_id").not_exists()
                ),
            )
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code")
            if error_code == "ConditionalCheckFailedException":
                raise FeatureSetConflict(
                    f"Feature set '{feature_set.feature_set_name}' already exists.",
                ) from exc
            logger.exception("Failed to create feature set")
            raise DataStoreError("Failed to persist feature set.") from exc
        except BotoCoreError as exc:
            logger.exception("Failed to create feature set")
            raise DataStoreError("Failed to persist feature set.") from exc

        return self._item_to_feature_response(item)

    def get_feature_set(self, tenant_id: str, feature_set_name: str) -> FeatureSetResponse:
        """Fetch a single tenant-scoped feature set.

        Args:
            tenant_id: Owning tenant identifier.
            feature_set_name: Feature-set name.

        Returns:
            The stored feature set.

        Raises:
            FeatureSetNotFound: If the feature set does not exist.
            DataStoreError: If DynamoDB fails unexpectedly.
        """
        try:
            response = self._table.get_item(
                Key={
                    "tenant_id": tenant_id,
                    "resource_id": self._feature_resource_id(feature_set_name),
                },
                ConsistentRead=True,
            )
        except (BotoCoreError, ClientError) as exc:
            logger.exception("Failed to read feature set")
            raise DataStoreError("Failed to read feature set.") from exc

        item = response.get("Item")
        if not item or item.get("entity_type") != "FEATURE_SET":
            raise FeatureSetNotFound(f"Feature set '{feature_set_name}' was not found.")

        return self._item_to_feature_response(item)

    def list_feature_sets(self, tenant_id: str) -> list[FeatureSetResponse]:
        """List all feature sets for a tenant.

        Args:
            tenant_id: Owning tenant identifier.

        Returns:
            A list of feature sets belonging to the tenant.

        Raises:
            DataStoreError: If DynamoDB fails unexpectedly.
        """
        items: list[dict[str, Any]] = []
        exclusive_start_key: dict[str, Any] | None = None

        try:
            while True:
                query_kwargs: dict[str, Any] = {
                    "KeyConditionExpression": Key("tenant_id").eq(tenant_id)
                    & Key("resource_id").begins_with(_FEATURE_PREFIX),
                    "ConsistentRead": True,
                }
                if exclusive_start_key is not None:
                    query_kwargs["ExclusiveStartKey"] = exclusive_start_key

                response = self._table.query(**query_kwargs)
                items.extend(response.get("Items", []))
                exclusive_start_key = response.get("LastEvaluatedKey")
                if exclusive_start_key is None:
                    break
        except (BotoCoreError, ClientError) as exc:
            logger.exception("Failed to list feature sets")
            raise DataStoreError("Failed to list feature sets.") from exc

        return [self._item_to_feature_response(item) for item in items]

    def delete_feature_set(self, tenant_id: str, feature_set_name: str) -> None:
        """Delete a tenant-scoped feature set.

        Args:
            tenant_id: Owning tenant identifier.
            feature_set_name: Feature-set name.

        Raises:
            FeatureSetNotFound: If the feature set does not exist.
            DataStoreError: If DynamoDB fails unexpectedly.
        """
        try:
            self._table.delete_item(
                Key={
                    "tenant_id": tenant_id,
                    "resource_id": self._feature_resource_id(feature_set_name),
                },
                ConditionExpression=(
                    Attr("tenant_id").exists() & Attr("resource_id").exists()
                ),
            )
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code")
            if error_code == "ConditionalCheckFailedException":
                raise FeatureSetNotFound(f"Feature set '{feature_set_name}' was not found.") from exc
            logger.exception("Failed to delete feature set")
            raise DataStoreError("Failed to delete feature set.") from exc
        except BotoCoreError as exc:
            logger.exception("Failed to delete feature set")
            raise DataStoreError("Failed to delete feature set.") from exc

    def batch_get_feature_sets(
        self,
        tenant_id: str,
        feature_set_names: Sequence[str],
    ) -> list[FeatureSetResponse]:
        """Batch-fetch feature sets for the inference pipeline.

        Args:
            tenant_id: Owning tenant identifier.
            feature_set_names: Feature-set names to retrieve.

        Returns:
            A list of found feature sets in request order where possible.

        Raises:
            DataStoreError: If DynamoDB fails unexpectedly.
        """
        if not feature_set_names:
            return []

        requested_keys = [
            {
                "tenant_id": self._serializer.serialize(tenant_id),
                "resource_id": self._serializer.serialize(
                    self._feature_resource_id(feature_set_name),
                ),
            }
            for feature_set_name in feature_set_names
        ]
        request_items: dict[str, Any] = {
            self._table_name: {
                "Keys": requested_keys,
                "ConsistentRead": True,
            },
        }

        raw_items: list[dict[str, Any]] = []
        retries = 0
        while request_items:
            try:
                response = self._client.batch_get_item(RequestItems=request_items)
            except (BotoCoreError, ClientError) as exc:
                logger.exception("Failed to batch-read feature sets")
                raise DataStoreError("Failed to batch-read feature sets.") from exc

            raw_items.extend(response.get("Responses", {}).get(self._table_name, []))
            unprocessed = response.get("UnprocessedKeys", {})
            if not unprocessed:
                break

            retries += 1
            if retries > _BATCH_RETRY_LIMIT:
                logger.error("DynamoDB returned unprocessed keys after retries")
                raise DataStoreError("Failed to batch-read feature sets.")

            sleep(0.05 * (2**retries))
            request_items = unprocessed

        items = [self._deserialize_item(item) for item in raw_items]
        by_name = {
            cast(str, item["feature_set_name"]): self._item_to_feature_response(item)
            for item in items
            if item.get("entity_type") == "FEATURE_SET"
        }

        return [by_name[name] for name in feature_set_names if name in by_name]

    def get_tenant_record(self, tenant_id: str) -> Tenant:
        """Read a tenant record by tenant ID.

        Args:
            tenant_id: Tenant identifier.

        Returns:
            The tenant record.

        Raises:
            TenantNotFound: If the tenant does not exist.
            DataStoreError: If DynamoDB fails unexpectedly.
        """
        try:
            response = self._table.get_item(
                Key={
                    "tenant_id": tenant_id,
                    "resource_id": self._tenant_resource_id(tenant_id),
                },
                ConsistentRead=True,
            )
        except (BotoCoreError, ClientError) as exc:
            logger.exception("Failed to read tenant record")
            raise DataStoreError("Failed to read tenant record.") from exc

        item = response.get("Item")
        if not item or item.get("entity_type") != "TENANT":
            raise TenantNotFound(f"Tenant '{tenant_id}' was not found.")

        return Tenant(
            tenant_id=cast(str, item["tenant_id"]),
            api_key=cast(str, item["api_key"]),
            created_at=cast(str, item["created_at"]),
            is_active=bool(item.get("is_active", True)),
            allowed_models=list(item.get("allowed_models", [])),
        )

    @staticmethod
    def _feature_resource_id(feature_set_name: str) -> str:
        """Build the sort key for a feature set.

        Args:
            feature_set_name: Feature-set name.

        Returns:
            DynamoDB sort-key value.
        """
        return f"{_FEATURE_PREFIX}{feature_set_name}"

    @staticmethod
    def _tenant_resource_id(tenant_id: str) -> str:
        """Build the sort key for a tenant record.

        Args:
            tenant_id: Tenant identifier.

        Returns:
            DynamoDB sort-key value.
        """
        return f"{_TENANT_PREFIX}{tenant_id}"

    def _item_to_feature_response(self, item: dict[str, Any]) -> FeatureSetResponse:
        """Convert a DynamoDB item into a response model.

        Args:
            item: Raw DynamoDB item.

        Returns:
            A validated ``FeatureSetResponse``.
        """
        return FeatureSetResponse(
            tenant_id=cast(str, item["tenant_id"]),
            feature_set_name=cast(str, item["feature_set_name"]),
            features=cast(dict[str, float], self._from_decimal(item["features"])),
            created_at=cast(str, item["created_at"]),
            updated_at=cast(str, item["updated_at"]),
            version=int(item["version"]),
        )

    def _deserialize_item(self, item: dict[str, Any]) -> dict[str, Any]:
        """Deserialize a low-level DynamoDB response item.

        Args:
            item: Low-level AttributeValue mapping.

        Returns:
            A plain-Python representation of the item.
        """
        return {
            key: self._deserializer.deserialize(value)
            for key, value in item.items()
        }

    def _to_decimal(self, value: Any) -> Any:
        """Recursively convert floats to ``Decimal`` for DynamoDB.

        Args:
            value: Python value.

        Returns:
            A DynamoDB-compatible value.
        """
        if isinstance(value, dict):
            return {key: self._to_decimal(subvalue) for key, subvalue in value.items()}
        if isinstance(value, list):
            return [self._to_decimal(subvalue) for subvalue in value]
        if isinstance(value, float):
            return Decimal(str(value))
        return value

    def _from_decimal(self, value: Any) -> Any:
        """Recursively convert ``Decimal`` values to floats for API responses.

        Args:
            value: DynamoDB value.

        Returns:
            An API-friendly Python value.
        """
        if isinstance(value, dict):
            return {key: self._from_decimal(subvalue) for key, subvalue in value.items()}
        if isinstance(value, list):
            return [self._from_decimal(subvalue) for subvalue in value]
        if isinstance(value, Decimal):
            return float(value)
        return value
