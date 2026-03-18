"""Tests for the host-side enclave client."""

from __future__ import annotations

import base64
import json
import struct
from collections.abc import Callable
from io import BytesIO
from typing import Any

import joblib
import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sklearn.datasets import load_iris
from sklearn.ensemble import RandomForestClassifier

from feature_store.services.enclave_client import (
    EnclaveClient,
    EnclaveTimeoutError,
    MalformedEnclaveResponseError,
    MockEnclaveClient,
)


def _encode_response(payload: dict[str, Any]) -> bytes:
    encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return struct.pack("!I", len(encoded)) + encoded


class FakeSocket:
    """Minimal socket double for vsock client tests."""

    def __init__(
        self,
        *,
        response_bytes: bytes = b"",
        recv_error: Exception | None = None,
        send_error: Exception | None = None,
    ) -> None:
        self._response_bytes = bytearray(response_bytes)
        self._recv_error = recv_error
        self._send_error = send_error
        self.sent_frames: list[bytes] = []
        self.timeout: float | None = None
        self.closed = False

    def settimeout(self, value: float) -> None:
        self.timeout = value

    def connect(self, _address: tuple[int, int]) -> None:
        return None

    def sendall(self, data: bytes) -> None:
        if self._send_error is not None:
            raise self._send_error
        self.sent_frames.append(data)

    def recv(self, size: int) -> bytes:
        if self._recv_error is not None:
            raise self._recv_error
        if not self._response_bytes:
            return b""
        chunk = self._response_bytes[:size]
        del self._response_bytes[:size]
        return bytes(chunk)

    def close(self) -> None:
        self.closed = True


def _build_encrypted_model_material() -> tuple[bytes, bytes, Callable[[bytes, str], bytes]]:
    iris = load_iris()
    model = RandomForestClassifier(n_estimators=10, random_state=42)
    model.fit(iris.data, iris.target)

    model_bytes_buffer = BytesIO()
    joblib.dump(model, model_bytes_buffer)
    model_bytes = model_bytes_buffer.getvalue()

    plaintext_key = bytes(range(32))
    encrypted_key = b"wrapped-demo-key"
    nonce = b"0123456789ab"
    aad = b"tenant-a:iris-model"
    ciphertext = AESGCM(plaintext_key).encrypt(nonce, model_bytes, aad)

    encrypted_model_blob = json.dumps(
        {
            "algorithm": "AESGCM",
            "tenant_id": "tenant-a",
            "model_name": "iris-model",
            "feature_dimension": 4,
            "nonce_b64": base64.b64encode(nonce).decode("ascii"),
            "ciphertext_b64": base64.b64encode(ciphertext).decode("ascii"),
            "aad_b64": base64.b64encode(aad).decode("ascii"),
        },
        separators=(",", ":"),
    ).encode("utf-8")

    def resolver(ciphertext_blob: bytes, tenant_id: str) -> bytes:
        assert ciphertext_blob == encrypted_key
        assert tenant_id == "tenant-a"
        return plaintext_key

    return encrypted_model_blob, encrypted_key, resolver


def test_mock_enclave_client_predict_and_health() -> None:
    """The mock client should mirror the real client interface."""
    encrypted_model_blob, encrypted_key, resolver = _build_encrypted_model_material()
    client = MockEnclaveClient(key_resolver=resolver)

    assert client.health_check() is True

    response = client.predict(
        "iris-model",
        [5.1, 3.5, 1.4, 0.2],
        encrypted_key,
        encrypted_model_blob=encrypted_model_blob,
        tenant_id="tenant-a",
    )

    assert response["status"] == "ok"
    assert response["confidence"] >= 0.0
    assert response["latency_ms"] >= 0.0
    assert response["served_from_cache"] is False

    second_response = client.predict(
        "iris-model",
        [5.1, 3.5, 1.4, 0.2],
        encrypted_key,
        tenant_id="tenant-a",
    )
    assert second_response["served_from_cache"] is True
    assert client.list_models() == ["iris-model"]


def test_timeout_handling_retries_then_fails() -> None:
    """Socket timeouts should be retried and surfaced as EnclaveTimeoutError."""
    attempts = 0

    def socket_factory(_cid: int, _port: int, _timeout: float) -> FakeSocket:
        nonlocal attempts
        attempts += 1
        return FakeSocket(recv_error=TimeoutError("timed out"))

    client = EnclaveClient(
        cid=16,
        port=5005,
        timeout_seconds=0.1,
        max_retries=3,
        socket_factory=socket_factory,
        sleep_fn=lambda _seconds: None,
    )

    with pytest.raises(EnclaveTimeoutError):
        client.send_request("health", {"echo": "ping"})

    assert attempts == 3


def test_retry_logic_succeeds_after_transient_failure() -> None:
    """The client should retry transient transport failures with backoff."""
    attempts = 0
    success_response = _encode_response({"status": "ok", "echo": "ping"})

    def socket_factory(_cid: int, _port: int, _timeout: float) -> FakeSocket:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return FakeSocket(send_error=OSError("temporary send failure"))
        return FakeSocket(response_bytes=success_response)

    client = EnclaveClient(
        cid=16,
        port=5005,
        timeout_seconds=1.0,
        max_retries=3,
        socket_factory=socket_factory,
        sleep_fn=lambda _seconds: None,
    )

    response = client.send_request("health", {"echo": "ping"})

    assert response["status"] == "ok"
    assert attempts == 2


def test_malformed_response_handling() -> None:
    """Invalid JSON frames should raise MalformedEnclaveResponseError."""
    malformed_payload = b"not-json"
    frame = struct.pack("!I", len(malformed_payload)) + malformed_payload

    client = EnclaveClient(
        cid=16,
        port=5005,
        timeout_seconds=1.0,
        max_retries=1,
        socket_factory=lambda _cid, _port, _timeout: FakeSocket(response_bytes=frame),
        sleep_fn=lambda _seconds: None,
    )

    with pytest.raises(MalformedEnclaveResponseError):
        client.send_request("health", {"echo": "ping"})
