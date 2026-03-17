"""Inference API scaffold tests."""

from fastapi import status


def test_inference_endpoint_is_scaffolded(client, tenant_a_headers, tenant_a) -> None:
    payload = {
        "tenant_id": tenant_a.tenant_id,
        "feature_set_name": "default",
        "model_name": "fraud-model",
    }

    response = client.post("/v1/inference", json=payload, headers=tenant_a_headers)

    assert response.status_code == status.HTTP_501_NOT_IMPLEMENTED
