"""CRUD and authentication tests for the feature store API."""

from __future__ import annotations

from fastapi import status


def test_feature_store_crud_flow(client, tenant_a_headers) -> None:
    """Create, read, list, and delete a feature set successfully."""
    create_payload = {
        "tenant_id": "tenant-a",
        "feature_set_name": "customer-profile",
        "features": {"age": 34.0, "balance": 1200.5},
    }

    create_response = client.post(
        "/features/",
        json=create_payload,
        headers=tenant_a_headers,
    )
    assert create_response.status_code == status.HTTP_201_CREATED
    created = create_response.json()
    assert created["tenant_id"] == "tenant-a"
    assert created["feature_set_name"] == "customer-profile"
    assert created["version"] == 1
    assert created["features"] == {"age": 34.0, "balance": 1200.5}

    get_response = client.get("/features/customer-profile", headers=tenant_a_headers)
    assert get_response.status_code == status.HTTP_200_OK
    fetched = get_response.json()
    assert fetched["feature_set_name"] == "customer-profile"
    assert fetched["features"] == {"age": 34.0, "balance": 1200.5}

    list_response = client.get("/features/", headers=tenant_a_headers)
    assert list_response.status_code == status.HTTP_200_OK
    listed = list_response.json()
    assert len(listed) == 1
    assert listed[0]["feature_set_name"] == "customer-profile"

    delete_response = client.delete("/features/customer-profile", headers=tenant_a_headers)
    assert delete_response.status_code == status.HTTP_204_NO_CONTENT

    missing_response = client.get("/features/customer-profile", headers=tenant_a_headers)
    assert missing_response.status_code == status.HTTP_404_NOT_FOUND
    assert missing_response.json()["error"] == "feature_set_not_found"


def test_tenant_isolation_prevents_cross_tenant_reads(
    client,
    tenant_a_headers,
    tenant_b_headers,
) -> None:
    """Tenant B cannot access tenant A resources by specifying tenant A explicitly."""
    create_payload = {
        "tenant_id": "tenant-a",
        "feature_set_name": "risk-features",
        "features": {"risk_score": 0.84},
    }
    create_response = client.post(
        "/features/",
        json=create_payload,
        headers=tenant_a_headers,
    )
    assert create_response.status_code == status.HTTP_201_CREATED

    cross_tenant_response = client.get(
        "/features/risk-features",
        headers=tenant_b_headers,
        params={"tenant_id": "tenant-a"},
    )
    assert cross_tenant_response.status_code == status.HTTP_403_FORBIDDEN
    assert cross_tenant_response.json()["error"] == "forbidden_access"


def test_invalid_api_key_is_rejected(client, invalid_api_key_headers) -> None:
    """Requests with an invalid API key should be rejected."""
    response = client.get("/features/", headers=invalid_api_key_headers)
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["error"] == "unauthorized_access"
    assert response.json()["detail"] == "Invalid tenant credentials."


def test_missing_headers_are_rejected(client) -> None:
    """Requests without tenant authentication headers should be rejected."""
    response = client.get("/features/")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    assert response.json()["error"] == "unauthorized_access"
    assert "Missing required authentication headers" in response.json()["detail"]
