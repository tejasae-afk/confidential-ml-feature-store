"""Cross-tenant attack tests."""

from __future__ import annotations

from fastapi import status


def test_cross_tenant_read_attempt_returns_403(
    client,
    tenant_a_headers,
    tenant_b_headers,
) -> None:
    """Tenant B cannot read tenant A's feature set when tenant A is specified."""
    payload = {
        "tenant_id": "tenant-a",
        "feature_set_name": "shared-name",
        "features": {"feature_x": 1.0},
    }
    create_response = client.post("/features/", json=payload, headers=tenant_a_headers)
    assert create_response.status_code == status.HTTP_201_CREATED

    response = client.get(
        "/features/shared-name",
        headers=tenant_b_headers,
        params={"tenant_id": "tenant-a"},
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN
    body = response.json()
    assert body["error"] == "forbidden_access"
    assert body["detail"] == "Tenant is not authorized to access the requested resource."


def test_cross_tenant_delete_attempt_returns_403(
    client,
    tenant_a_headers,
    tenant_b_headers,
) -> None:
    """Tenant B cannot delete tenant A's feature set when tenant A is specified."""
    payload = {
        "tenant_id": "tenant-a",
        "feature_set_name": "delete-target",
        "features": {"feature_y": 9.9},
    }
    create_response = client.post("/features/", json=payload, headers=tenant_a_headers)
    assert create_response.status_code == status.HTTP_201_CREATED

    response = client.delete(
        "/features/delete-target",
        headers=tenant_b_headers,
        params={"tenant_id": "tenant-a"},
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN
    body = response.json()
    assert body["error"] == "forbidden_access"
    assert body["detail"] == "Tenant is not authorized to access the requested resource."


def test_cross_tenant_list_attempt_returns_403(
    client,
    tenant_a_headers,
    tenant_b_headers,
) -> None:
    """Tenant B cannot list tenant A's feature sets when tenant A is specified."""
    payload = {
        "tenant_id": "tenant-a",
        "feature_set_name": "list-target",
        "features": {"feature_z": 2.5},
    }
    create_response = client.post("/features/", json=payload, headers=tenant_a_headers)
    assert create_response.status_code == status.HTTP_201_CREATED

    response = client.get(
        "/features/",
        headers=tenant_b_headers,
        params={"tenant_id": "tenant-a"},
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN
    body = response.json()
    assert body["error"] == "forbidden_access"
    assert body["detail"] == "Tenant is not authorized to access the requested resource."
