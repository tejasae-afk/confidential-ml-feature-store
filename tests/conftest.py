"""Shared pytest fixtures for the feature store tests."""

from __future__ import annotations

import os
from collections.abc import Generator
from datetime import datetime, timezone

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

from feature_store.config import get_settings
from feature_store.main import create_app
from feature_store.models.tenant import Tenant

os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("DYNAMODB_TABLE_NAME", "test-confidential-ml-feature-store")
os.environ.setdefault("KMS_KEY_ID", "test-kms-key")
os.environ.setdefault("ENCLAVE_CID", "16")
os.environ.setdefault("ENCLAVE_PORT", "5005")
os.environ.setdefault("LOG_LEVEL", "DEBUG")
os.environ.pop("DYNAMODB_ENDPOINT", None)


@pytest.fixture(autouse=True)
def clear_settings_cache() -> Generator[None, None, None]:
    """Clear cached settings before and after each test."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def aws_mock() -> Generator[None, None, None]:
    """Provide a moto-backed AWS context."""
    with mock_aws():
        yield


@pytest.fixture()
def dynamodb_table(aws_mock):
    """Create the DynamoDB table used by the application."""
    settings = get_settings()
    dynamodb = boto3.resource("dynamodb", region_name=settings.aws_region)
    table = dynamodb.create_table(
        TableName=settings.dynamodb_table_name,
        KeySchema=[
            {"AttributeName": "tenant_id", "KeyType": "HASH"},
            {"AttributeName": "resource_id", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "tenant_id", "AttributeType": "S"},
            {"AttributeName": "resource_id", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    table.wait_until_exists()
    return table


@pytest.fixture()
def tenant_a() -> Tenant:
    """Return the primary test tenant."""
    return Tenant(
        tenant_id="tenant-a",
        api_key="tenant-a-api-key",
        created_at=datetime.now(timezone.utc),
        is_active=True,
        allowed_models=["fraud-model", "churn-model"],
    )


@pytest.fixture()
def tenant_b() -> Tenant:
    """Return the secondary test tenant."""
    return Tenant(
        tenant_id="tenant-b",
        api_key="tenant-b-api-key",
        created_at=datetime.now(timezone.utc),
        is_active=True,
        allowed_models=["fraud-model"],
    )


@pytest.fixture()
def seed_tenants(dynamodb_table, tenant_a: Tenant, tenant_b: Tenant) -> None:
    """Seed the table with tenant records used for authentication."""
    for tenant in (tenant_a, tenant_b):
        dynamodb_table.put_item(
            Item={
                "tenant_id": tenant.tenant_id,
                "resource_id": f"TENANT#{tenant.tenant_id}",
                "entity_type": "TENANT",
                "api_key": tenant.api_key,
                "created_at": tenant.created_at.isoformat(),
                "is_active": tenant.is_active,
                "allowed_models": tenant.allowed_models,
            },
        )


@pytest.fixture()
def client(dynamodb_table, seed_tenants) -> Generator[TestClient, None, None]:
    """Return a FastAPI test client with app lifespan enabled."""
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def tenant_a_headers(tenant_a: Tenant) -> dict[str, str]:
    """Return authentication headers for tenant A."""
    return {"X-Tenant-ID": tenant_a.tenant_id, "X-API-Key": tenant_a.api_key}


@pytest.fixture()
def tenant_b_headers(tenant_b: Tenant) -> dict[str, str]:
    """Return authentication headers for tenant B."""
    return {"X-Tenant-ID": tenant_b.tenant_id, "X-API-Key": tenant_b.api_key}


@pytest.fixture()
def invalid_api_key_headers(tenant_a: Tenant) -> dict[str, str]:
    """Return headers with an invalid API key."""
    return {"X-Tenant-ID": tenant_a.tenant_id, "X-API-Key": "wrong-api-key"}
