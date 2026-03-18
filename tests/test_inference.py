"""End-to-end inference tests using the mock enclave client."""

from __future__ import annotations

import base64
import json
from io import BytesIO
from pathlib import Path
from typing import Any

import joblib
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import status
from sklearn.ensemble import RandomForestClassifier

from feature_store.services.enclave_client import MockEnclaveClient


def _train_small_classifier() -> RandomForestClassifier:
    model = RandomForestClassifier(n_estimators=10, random_state=42)
    training_features = [
        [0.1, 0.2, 0.3, 0.4],
        [0.2, 0.1, 0.4, 0.3],
        [0.9, 0.8, 0.7, 0.6],
        [0.8, 0.9, 0.6, 0.7],
    ]
    training_labels = [0, 0, 1, 1]
    model.fit(training_features, training_labels)
    return model


def _write_encrypted_model_artifacts(
    *,
    root: Path,
    tenant_id: str,
    model_name: str,
) -> dict[tuple[bytes, str], bytes]:
    model = _train_small_classifier()
    buffer = BytesIO()
    joblib.dump(model, buffer)
    model_bytes = buffer.getvalue()

    plaintext_key = bytes(range(32))
    encrypted_key = f"wrapped:{tenant_id}:{model_name}".encode()
    nonce = b"0123456789ab"
    aad = f"{tenant_id}:{model_name}".encode()
    ciphertext = AESGCM(plaintext_key).encrypt(nonce, model_bytes, aad)

    model_payload: dict[str, Any] = {
        "algorithm": "AESGCM",
        "tenant_id": tenant_id,
        "model_name": model_name,
        "feature_dimension": 4,
        "nonce_b64": base64.b64encode(nonce).decode("ascii"),
        "ciphertext_b64": base64.b64encode(ciphertext).decode("ascii"),
        "aad_b64": base64.b64encode(aad).decode("ascii"),
    }

    model_dir = root / tenant_id / model_name
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "encrypted_model.bin").write_bytes(
        json.dumps(model_payload, separators=(",", ":")).encode("utf-8"),
    )
    (model_dir / "encrypted_data_key.bin").write_bytes(encrypted_key)
    return {(encrypted_key, tenant_id): plaintext_key}


def _install_mock_enclave(client, key_map: dict[tuple[bytes, str], bytes]) -> None:
    def resolver(ciphertext_blob: bytes, tenant_id: str) -> bytes:
        return key_map[(ciphertext_blob, tenant_id)]

    client.app.state.enclave_client = MockEnclaveClient(key_resolver=resolver)


def test_end_to_end_inference_with_mock_enclave(
    client,
    tenant_a_headers,
    tenant_a,
    monkeypatch,
    tmp_path,
) -> None:
    """Create features, invoke inference, and receive a prediction."""
    key_map = _write_encrypted_model_artifacts(
        root=tmp_path,
        tenant_id=tenant_a.tenant_id,
        model_name="fraud-model",
    )
    monkeypatch.setenv("MODEL_STORAGE_DIR", str(tmp_path))
    _install_mock_enclave(client, key_map)

    create_response = client.post(
        "/features/",
        json={
            "tenant_id": tenant_a.tenant_id,
            "feature_set_name": "customer-a",
            "features": {"f1": 0.1, "f2": 0.2, "f3": 0.3, "f4": 0.4},
        },
        headers=tenant_a_headers,
    )
    assert create_response.status_code == status.HTTP_201_CREATED

    inference_response = client.post(
        "/inference/",
        json={
            "tenant_id": tenant_a.tenant_id,
            "feature_set_name": "customer-a",
            "model_name": "fraud-model",
        },
        headers=tenant_a_headers,
    )

    assert inference_response.status_code == status.HTTP_200_OK
    body = inference_response.json()
    assert body["prediction"] in {0.0, 1.0}
    assert 0.0 <= body["confidence"] <= 1.0
    assert body["latency_ms"] >= 0.0
    assert body["served_from_cache"] is False


def test_invalid_model_name_returns_404(
    client,
    tenant_a_headers,
    tenant_a,
    monkeypatch,
    tmp_path,
) -> None:
    """Missing encrypted artifacts should return a clear 404 error."""
    monkeypatch.setenv("MODEL_STORAGE_DIR", str(tmp_path))
    _install_mock_enclave(client, {})

    create_response = client.post(
        "/features/",
        json={
            "tenant_id": tenant_a.tenant_id,
            "feature_set_name": "customer-a",
            "features": {"f1": 0.1, "f2": 0.2, "f3": 0.3, "f4": 0.4},
        },
        headers=tenant_a_headers,
    )
    assert create_response.status_code == status.HTTP_201_CREATED

    inference_response = client.post(
        "/inference/",
        json={
            "tenant_id": tenant_a.tenant_id,
            "feature_set_name": "customer-a",
            "model_name": "missing-model",
        },
        headers=tenant_a_headers,
    )

    assert inference_response.status_code == status.HTTP_404_NOT_FOUND
    assert inference_response.json()["error"] == "model_artifact_not_found"


def test_feature_dimension_mismatch_returns_400(
    client,
    tenant_a_headers,
    tenant_a,
    monkeypatch,
    tmp_path,
) -> None:
    """Inference should fail cleanly when the feature vector size is wrong."""
    key_map = _write_encrypted_model_artifacts(
        root=tmp_path,
        tenant_id=tenant_a.tenant_id,
        model_name="fraud-model",
    )
    monkeypatch.setenv("MODEL_STORAGE_DIR", str(tmp_path))
    _install_mock_enclave(client, key_map)

    create_response = client.post(
        "/features/",
        json={
            "tenant_id": tenant_a.tenant_id,
            "feature_set_name": "customer-short",
            "features": {"f1": 0.1, "f2": 0.2, "f3": 0.3},
        },
        headers=tenant_a_headers,
    )
    assert create_response.status_code == status.HTTP_201_CREATED

    inference_response = client.post(
        "/inference/",
        json={
            "tenant_id": tenant_a.tenant_id,
            "feature_set_name": "customer-short",
            "model_name": "fraud-model",
        },
        headers=tenant_a_headers,
    )

    assert inference_response.status_code == status.HTTP_400_BAD_REQUEST
    assert inference_response.json()["error"] == "feature_dimension_mismatch"


def test_cross_tenant_inference_is_denied(
    client,
    tenant_a_headers,
    tenant_b_headers,
    tenant_a,
    monkeypatch,
    tmp_path,
) -> None:
    """A tenant must not trigger inference on another tenant's feature set."""
    key_map = _write_encrypted_model_artifacts(
        root=tmp_path,
        tenant_id=tenant_a.tenant_id,
        model_name="fraud-model",
    )
    monkeypatch.setenv("MODEL_STORAGE_DIR", str(tmp_path))
    _install_mock_enclave(client, key_map)

    create_response = client.post(
        "/features/",
        json={
            "tenant_id": tenant_a.tenant_id,
            "feature_set_name": "customer-a",
            "features": {"f1": 0.1, "f2": 0.2, "f3": 0.3, "f4": 0.4},
        },
        headers=tenant_a_headers,
    )
    assert create_response.status_code == status.HTTP_201_CREATED

    inference_response = client.post(
        "/inference/",
        json={
            "tenant_id": tenant_a.tenant_id,
            "feature_set_name": "customer-a",
            "model_name": "fraud-model",
        },
        headers=tenant_b_headers,
    )

    assert inference_response.status_code == status.HTTP_403_FORBIDDEN
    assert inference_response.json()["error"] == "forbidden_access"
